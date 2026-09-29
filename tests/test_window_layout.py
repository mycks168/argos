"""labwc配置アダプターの安全性と矩形計算を検証する。"""

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from argos.tools import window_layout as layout

PACKAGE = "com.google.android.apps.maps"


@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize("ratio", [20, 40, 50, 80])
def test_geometry(side, ratio):
    """二つの領域が画面を隙間なく分割する。"""
    android, argos = layout.geometry(1920, 440, ratio, side)
    left, right = sorted([android, argos])
    assert left[0] == 0
    assert left[2] == right[0]
    assert right[0] + right[2] == 1920
    assert android[2] == round(1920 * ratio / 100)


@pytest.mark.parametrize("values", [(1920, 440, 19, "left"), (1920, 440, 81, "left"), (1920, 440, 50, "bad"), (0, 440, 50, "left"), (1920, 0, 50, "left")])
def test_invalid_geometry(values):
    """範囲外の指定を拒否する。"""
    with pytest.raises(ValueError):
        layout.geometry(*values)


@pytest.mark.parametrize("mode", ["split", "android", "argos"])
@pytest.mark.parametrize("namespace", ["", "http://openbox.org/3.4/rc"])
def test_binding(mode, namespace):
    """既存キーを保持し、対象アプリを限定する。"""
    ns = f' xmlns="{namespace}"' if namespace else ""
    root = ET.fromstring(f'<openbox_config{ns}><keyboard><keybind key="W-x"/><keybind key="W-C-A-F12"/></keyboard></openbox_config>')
    result = layout.build_binding(root, {"mode": mode, "ratio": 40, "side": "left", "android_app": "com.google.android.apps.maps"}, 1920, 440, ("android",))
    assert sum(node.get("key") == "W-C-A-F12" for node in result.iter()) == 1
    assert any(node.get("key") == "W-x" for node in result.iter())
    queries = [node.attrib for node in result.iter() if str(node.tag).endswith("query")]
    assert {"identifier": "waydroid.com.google.android.apps.maps"} in queries
    assert {"identifier": "*chrom*", "title": "ARGOS Dashboard"} in queries
    assert not any("fullscreen" in query for query in queries)
    # タイル状態のウィンドウは移動できないため、配置の前に必ず解除する。
    names = [node.get("name") for node in result.iter() if str(node.tag).endswith("action")]
    assert names.count("UnSnap") == names.count("ResizeTo") == names.count("MoveTo")


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    """実機を操作せずコマンドの実行順と設定を記録する。"""
    monkeypatch.setattr(layout.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("ARGOS_CONFIG_FILE", str(tmp_path / "config.yaml"))
    monkeypatch.setenv(layout.APP_SETTING, "maps")
    # 他のテストが環境変数へ取り込んだ設定に左右されないよう、配置の設定は毎回消す。
    for name in (layout.VIEW_SETTING, layout.STYLE_SETTING, layout.RATIO_SETTING, layout.PANEL_HEIGHT_SETTING, layout.RESTART_SERVICES_SETTING, layout.DASHBOARD_LAYOUT_SETTING, layout.SWAP_SETTING):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(layout.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(layout.subprocess, "check_output", lambda *args, **kwargs: "123\n")
    monkeypatch.setattr(layout.time, "sleep", lambda seconds: None)
    # 実機の画面・Androidの状態には依存させない。個別のテストで必要に応じて上書きする。
    monkeypatch.setattr(layout, "detect_display", lambda: (1920, 440))
    monkeypatch.setattr(layout, "android_size", lambda: None)
    # 実機のダッシュボードへは接続しない。会話欄は既に右にある状態を返す。
    monkeypatch.setattr(layout, "dashboard_call", lambda path, payload=None: {"slot_stacks": {"center": [{"type": "notifications"}], "right": [{"type": "conversation"}]}})
    calls = []

    def run(command, **kwargs):
        """外部操作を記録する。"""
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(layout.subprocess, "run", run)
    config = tmp_path / ".config/labwc/rc.xml"
    config.parent.mkdir(parents=True)
    config.write_text('<openbox_config><!-- 保持する --><touch mouseEmulation="yes"/></openbox_config>')
    return tmp_path, config, calls


@pytest.mark.parametrize("mode", ["split", "android", "argos", "restore", "swap", "status"])
def test_main(desktop, monkeypatch, mode):
    """保存と復元、元設定の完全な保持を確認する。"""
    home, config, calls = desktop
    original = config.read_bytes()
    monkeypatch.setattr(sys, "argv", ["layout", mode, "--ratio", "40", "--side", "right"])
    layout.main()
    assert config.read_bytes() == original
    if mode == "status":
        assert not calls
        return
    saved = json.loads((home / ".local/state/argos/window-layout.json").read_text())
    assert saved["ratio"] == 40
    assert saved["side"] == ("left" if mode == "swap" else "right")
    assert calls[-1] == ["labwc", "-r"]
    monkeypatch.setattr(sys, "argv", ["layout", "restore"])
    layout.main()
    assert config.read_bytes() == original


def test_failure_restores_config(desktop, monkeypatch):
    """入力送信失敗時にも設定を復元し、成功状態を保存しない。"""
    home, config, _ = desktop
    original = config.read_bytes()

    def fail_input(command, **kwargs):
        """仮想キー送信だけを失敗させる。"""
        if command[0] == "wtype":
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(layout.subprocess, "run", fail_input)
    monkeypatch.setattr(sys, "argv", ["layout", "split"])
    with pytest.raises(subprocess.CalledProcessError):
        layout.main()
    assert config.read_bytes() == original
    assert not (home / ".local/state/argos/window-layout.json").exists()


@pytest.mark.parametrize("reason", ["missing_tool", "multiple_sessions", "used_key"])
def test_preflight(desktop, monkeypatch, reason):
    """操作対象や依存関係が不明な場合は設定を書き換えない。"""
    _, config, _ = desktop
    if reason == "missing_tool":
        monkeypatch.setattr(layout.shutil, "which", lambda tool: None)
    elif reason == "multiple_sessions":
        monkeypatch.setattr(layout.subprocess, "check_output", lambda *args, **kwargs: "123\n456\n")
    else:
        config.write_text('<openbox_config><keyboard><keybind key="W-C-A-F12"/></keyboard></openbox_config>')
    original = config.read_bytes()
    monkeypatch.setattr(sys, "argv", ["layout", "split"])
    with pytest.raises(RuntimeError):
        layout.main()
    assert config.read_bytes() == original


@pytest.fixture
def android(monkeypatch):
    """Waydroidの凍結状態とウィンドウ有無を模擬し、実行コマンドを記録する。"""
    monkeypatch.setattr(layout.time, "sleep", lambda seconds: None)
    world = {"frozen": False, "window": True, "launches": 0, "appear_after": 1, "calls": []}

    def status(command, **kwargs):
        """waydroid statusの出力を返す。pgrepは単一のlabwcとして答える。"""
        if command[0] == "pgrep":
            return "123\n"
        return f"Session:\tRUNNING\nContainer:\t{'FROZEN' if world['frozen'] else 'RUNNING'}\n"

    def run(command, **kwargs):
        """起動は凍結を解除し、appear_after回目の起動でウィンドウを出す。"""
        world["calls"].append(command)
        if command[:3] == ["waydroid", "app", "launch"]:
            world["launches"] += 1
            world["frozen"] = False
            world["window"] = world["launches"] >= world["appear_after"]
            return SimpleNamespace(returncode=0)
        android_query = any(str(part).startswith("app_id:") for part in command)
        argos_query = any(str(part).startswith("title:") for part in command)
        if argos_query and not world.get("argos", True):
            return SimpleNamespace(returncode=1)
        return SimpleNamespace(returncode=0 if world["window"] or not android_query else 1)

    monkeypatch.setattr(layout.subprocess, "check_output", status)
    monkeypatch.setattr(layout.subprocess, "run", run)
    return world


def test_ensure_android_noop(android):
    """凍結しておらずウィンドウがあれば起動しない。"""
    layout.ensure_android(PACKAGE)
    assert android["launches"] == 0


def test_ensure_android_unfreezes(android):
    """凍結中は起動処理で解除され、ウィンドウがあっても起動し直す。"""
    android["frozen"] = True
    layout.ensure_android(PACKAGE)
    assert android["launches"] == 1
    assert not android["frozen"]


def test_ensure_android_retries(android):
    """ホームだけ表示された場合は再起動し、ウィンドウの出現まで確認する。"""
    android.update(window=False, appear_after=2)
    layout.ensure_android(PACKAGE)
    assert android["launches"] == 2


def test_ensure_android_gives_up(android):
    """ウィンドウが出なければ成功扱いにしない。"""
    android.update(window=False, appear_after=99)
    with pytest.raises(RuntimeError):
        layout.ensure_android(PACKAGE, attempts=2, wait=2)
    assert android["launches"] == 2


def test_container_frozen_unknown(monkeypatch):
    """状態を取得できない場合は凍結とみなさない。"""
    def fail(*args, **kwargs):
        """waydroidコマンドの失敗を模擬する。"""
        raise FileNotFoundError

    monkeypatch.setattr(layout.subprocess, "check_output", fail)
    assert layout.container_frozen() is False


def test_state_defaults(tmp_path, monkeypatch):
    """旧形式の保存ファイルの余分な項目は無視し、不足は既定値で補う。"""
    monkeypatch.setenv("ARGOS_CONFIG_FILE", str(tmp_path / "config.yaml"))
    for name in (layout.STYLE_SETTING, layout.RATIO_SETTING):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "state.json"
    path.write_text('{"ratio": 30, "android_app": "old.package"}')
    assert layout.load_state(path) == {"ratio": 30, "side": "left", "mode": "split", "pane": None, "panel_hidden": False, "return_mode": None}


def test_configured_package_sources(tmp_path, monkeypatch):
    """空でない環境変数が優先され、空ならconfig.yamlを読む。どちらも空なら未設定。"""
    config = tmp_path / "config.yaml"
    config.write_text("window_layout:\n  android_app: maps\n")
    monkeypatch.setenv("ARGOS_CONFIG_FILE", str(config))
    monkeypatch.delenv(layout.APP_SETTING, raising=False)
    assert layout.configured_package() == PACKAGE
    monkeypatch.setenv(layout.APP_SETTING, "")
    assert layout.configured_package() == PACKAGE
    config.write_text("window_layout:\n  android_app: ''\n")
    assert layout.configured_package() is None


def test_configured_package_rejects_unknown(monkeypatch):
    """登録のないアプリ名は拒否する。"""
    monkeypatch.setenv(layout.APP_SETTING, "com.example.other")
    with pytest.raises(RuntimeError):
        layout.configured_package()


def test_boot_reapplies_saved_state(desktop, monkeypatch):
    """前回の配置が保存され、bootで同じ状態を再適用する。"""
    home, config, calls = desktop
    monkeypatch.setattr(sys, "argv", ["layout", "swap"])
    layout.main()
    saved = home / ".local/state/argos/window-layout.json"
    assert "android_app" not in json.loads(saved.read_text())
    calls.clear()
    monkeypatch.setattr(sys, "argv", ["layout", "boot"])
    layout.main()
    state = json.loads(saved.read_text())
    assert (state["side"], state["mode"]) == ("right", "split")
    assert ["labwc", "-r"] in calls


def test_boot_keeps_argos_mode_without_launching_android(desktop, android, monkeypatch):
    """前回がARGOS全画面なら、bootは地図を起動しない。"""
    monkeypatch.setattr(sys, "argv", ["layout", "argos"])
    layout.main()
    android.update(window=False)
    monkeypatch.setattr(sys, "argv", ["layout", "boot"])
    layout.main()
    assert android["launches"] == 0


@pytest.mark.parametrize("reason", ["no_app", "not_labwc"])
def test_boot_skips_quietly(desktop, monkeypatch, capsys, reason):
    """設定なしやlabwc以外の端末では、bootは何も変更せず成功する。"""
    _, config, calls = desktop
    if reason == "no_app":
        monkeypatch.setenv(layout.APP_SETTING, "")
    else:
        monkeypatch.setattr(layout.shutil, "which", lambda tool: None)
    original = config.read_bytes()
    monkeypatch.setattr(sys, "argv", ["layout", "boot"])
    layout.main()
    assert "skipped" in capsys.readouterr().out
    assert config.read_bytes() == original
    assert ["labwc", "-r"] not in calls


@pytest.mark.parametrize("pids", ["", "123\n456\n", None])
def test_boot_session_not_ready_remains_retryable(desktop, monkeypatch, pids):
    """起動途中のセッション不確定を成功扱いせず、サービスの再試行へ渡す。"""
    _, config, calls = desktop
    original = config.read_bytes()

    def sessions(*args, **kwargs):
        """セッション未起動または複数存在を再現する。"""
        if pids is None:
            raise subprocess.CalledProcessError(1, "pgrep")
        return pids

    monkeypatch.setattr(layout.subprocess, "check_output", sessions)
    monkeypatch.setattr(sys, "argv", ["layout", "boot"])
    with pytest.raises(RuntimeError, match="一意"):
        layout.main()
    assert config.read_bytes() == original
    assert ["labwc", "-r"] not in calls


def test_split_requires_android_app(desktop, monkeypatch):
    """Android未設定でsplitを要求すると設定を変えず拒否する。"""
    _, config, _ = desktop
    monkeypatch.setenv(layout.APP_SETTING, "")
    original = config.read_bytes()
    monkeypatch.setattr(sys, "argv", ["layout", "split"])
    with pytest.raises(RuntimeError):
        layout.main()
    assert config.read_bytes() == original


def test_argos_without_android(desktop, monkeypatch):
    """Android未設定でもARGOSの全画面化はできる。"""
    _, config, _ = desktop
    monkeypatch.setenv(layout.APP_SETTING, "")
    original = config.read_bytes()
    monkeypatch.setattr(sys, "argv", ["layout", "argos"])
    layout.main()
    assert config.read_bytes() == original


@pytest.mark.parametrize(
    "width, expected",
    [
        (1920, [413, 12, 1004, 416]),  # 実機の画面ダンプで測った中央ペインの位置と幅。上下は枠の分だけ内側
        (1280, [275, 12, 669, 416]),
        (900, [206, 12, 430, 416]),  # 761〜900pxは幅の狭い画面用の列定義
    ],
)
def test_center_pane_matches_css_grid(width, expected):
    """中央ペインの想定サイズは、ダッシュボードのgrid計算と一致する。"""
    assert layout.center_pane(width, 440) == expected


def test_center_pane_min_width_takes_priority():
    """fr按分で最小幅を下回る列は最小幅に固定し、残りを再按分する。"""
    x, _, w, _ = layout.center_pane(1250, 440)
    assert w == 660
    assert 260 < x < 270


@pytest.mark.parametrize("width", [901, 1000, 1200])
def test_center_pane_rejects_width_below_minimums(width):
    """最小幅の合計に足りない画面幅は、はみ出すため拒否する。"""
    with pytest.raises(ValueError):
        layout.center_pane(width, 440)


def test_center_pane_rejects_narrow_screen():
    """3分割にならない狭い画面は未対応として拒否する。"""
    with pytest.raises(ValueError):
        layout.center_pane(760, 440)


@pytest.mark.parametrize(
    "size, expected",
    [
        (None, [413, 12, 1004, 416]),
        ([1004, 416], [413, 12, 1004, 416]),
        ([1004, 440], [413, 12, 1004, 416]),
        ([960, 416], [435, 12, 960, 416]),
        ([1200, 600], [413, 12, 1004, 416]),
    ],
)
def test_fit_android(size, expected):
    """Androidの実サイズをペイン内に中央寄せし、ペインからはみ出さない。"""
    assert layout.fit_android([413, 12, 1004, 416], size) == expected


def _next_layer(layer, action):
    """labwcのToggleAlwaysOnTop/Bottomの層遷移を模擬する。"""
    if action == "Focus":
        return layer
    target = "top" if action == "ToggleAlwaysOnTop" else "bottom"
    return "normal" if layer == target else target


@pytest.mark.parametrize("start", ["normal", "top", "bottom"])
def test_layer_sequences_are_idempotent(start):
    """どの層から始めても、最前面固定・通常復帰の結果が決まる。"""
    for sequence, expected in ((layout.LAYER_TOP, "top"), (layout.LAYER_NORMAL, "normal")):
        layer = start
        for action in sequence:
            layer = _next_layer(layer, action)
        assert layer == expected


@pytest.mark.parametrize("sequence", ["LAYER_TOP", "LAYER_NORMAL"])
def test_layer_sequences_focus_target_first(sequence):
    """層の切り替えはフォーカス中の窓に効くため、必ず先にFocusする。"""
    assert getattr(layout, sequence)[0] == "Focus"


def test_pane_binding_pins_android_on_top_and_fullscreens_argos():
    """paneモードはARGOSを全画面にし、Androidを最前面へ固定して指定位置へ置く。"""
    root = ET.fromstring("<openbox_config><keyboard/></openbox_config>")
    state = {"mode": "pane", "ratio": 50, "side": "left", "android_app": PACKAGE, "pane": [435, 0, 960, 440]}
    result = layout.build_binding(root, state, 1920, 440, ())
    foreach = [node for node in result.iter() if node.get("name") == "ForEach"]
    argos, android = ([child.get("name") for child in node.find("then")] for node in foreach)
    assert argos == ["ToggleFullscreen", "Raise"]
    assert android[:len(layout.LAYER_TOP)] == list(layout.LAYER_TOP)
    assert android[-2:] == ["MoveTo", "Raise"]
    move = next(node for node in result.iter() if node.get("name") == "MoveTo")
    assert (move.get("x"), move.get("y")) == ("435", "0")


def test_pane_binding_keeps_fullscreen_argos():
    """既に全画面のARGOSは全画面を切り替えない。"""
    root = ET.fromstring("<openbox_config><keyboard/></openbox_config>")
    state = {"mode": "pane", "ratio": 50, "side": "left", "android_app": PACKAGE, "pane": [0, 0, 960, 440]}
    result = layout.build_binding(root, state, 1920, 440, ("argos",))
    argos = [child.get("name") for child in next(node for node in result.iter() if node.get("name") == "ForEach").find("then")]
    assert argos == ["Raise"]


@pytest.mark.parametrize("value, expected", [("auto", None), ("1,2,3,4", [1, 2, 3, 4])])
def test_parse_pane(value, expected):
    """autoは自動計算、x,y,w,hは検証済みの矩形になる。"""
    assert layout.parse_pane(value, 1920, 440) == expected


@pytest.mark.parametrize("value", ["1,2,3", "a,b,c,d", "-1,0,10,10", "0,0,0,10", "1000,0,1000,10", "0,0,10,441"])
def test_parse_pane_rejects_invalid(value):
    """形式不正や画面外の矩形を拒否する。"""
    with pytest.raises(ValueError):
        layout.parse_pane(value, 1920, 440)


def test_android_size(monkeypatch):
    """waydroidのプロパティからAndroidの描画サイズを読む。"""
    values = {"persist.waydroid.width": "1004\n", "persist.waydroid.height": "440\n"}
    monkeypatch.setattr(layout.subprocess, "check_output", lambda command, **kwargs: values[command[-1]])
    assert layout.android_size() == [1004, 440]


@pytest.mark.parametrize("output", ["", "\n", "abc\n", "0\n"])
def test_android_size_unknown(monkeypatch, output):
    """値が読めない・不正なら不明(None)として扱う。"""
    monkeypatch.setattr(layout.subprocess, "check_output", lambda command, **kwargs: output)
    assert layout.android_size() is None


def test_android_size_command_failure(monkeypatch):
    """waydroidコマンドが使えなければ不明(None)として扱う。"""
    def fail(*args, **kwargs):
        """コマンドがない状態を模擬する。"""
        raise FileNotFoundError

    monkeypatch.setattr(layout.subprocess, "check_output", fail)
    assert layout.android_size() is None


@pytest.fixture
def panel(monkeypatch):
    """パネルの起動状態を模擬し、停止・起動の操作を記録する。"""
    world = {"running": True, "started": 0, "killed": []}
    real_run = layout.subprocess.run

    def run(command, **kwargs):
        """pgrepは状態を返し、pkillは停止として記録する。それ以外は元の模擬に任せる。"""
        if command[0] == "pgrep" and command[-1] == "wf-panel-pi":
            return SimpleNamespace(returncode=0 if world["running"] else 1)
        if command[0] == "pkill":
            world["killed"].append(command)
            if command[-1] == "wf-panel-pi":
                world["running"] = False
            return SimpleNamespace(returncode=0)
        return real_run(command, **kwargs)

    def popen(command, **kwargs):
        """パネルの起動を記録する。"""
        world["started"] += 1
        world["running"] = True

    monkeypatch.setattr(layout.subprocess, "run", run)
    monkeypatch.setattr(layout.subprocess, "Popen", popen)
    return world


def test_hide_panel_stops_respawner_first(panel):
    """見張り役(lwrespawn)を先に止めてからパネルを止める。"""
    layout.hide_panel()
    assert "lwrespawn" in panel["killed"][0][-1]
    assert panel["killed"][1][-1] == "wf-panel-pi"


def test_show_panel_uses_respawner_when_available(desktop, monkeypatch):
    """lwrespawnがあれば、それ経由でパネルを起動する。"""
    started = []
    monkeypatch.setattr(layout.subprocess, "Popen", lambda command, **kwargs: started.append(command))
    layout.show_panel()
    assert started == [["/usr/bin/lwrespawn", layout.PANEL_BINARY]]
    monkeypatch.setattr(layout.shutil, "which", lambda tool: None)
    layout.show_panel()
    assert started[-1] == [layout.PANEL_BINARY]


def test_pane_mode_hides_panel_and_fits_android(desktop, panel, monkeypatch, capsys):
    """paneモードはパネルを止め、Androidの実サイズをペインの中央に置く。"""
    home, config, _ = desktop
    original = config.read_bytes()
    monkeypatch.setattr(layout, "android_size", lambda: [960, 416])
    monkeypatch.setattr(sys, "argv", ["layout", "pane"])
    layout.main()
    report = json.loads(capsys.readouterr().out)
    assert report["pane_rect"] == [413, 12, 1004, 416]
    assert report["android_rect"] == [435, 12, 960, 416]
    assert report["android_fit"] is False
    assert report["panel_hidden"] is True and not panel["running"]
    assert config.read_bytes() == original


def test_pane_mode_fits_exactly_when_android_matches(desktop, panel, monkeypatch, capsys):
    """Androidの解像度がペインと同じなら、ペインいっぱいに置く。"""
    monkeypatch.setattr(layout, "android_size", lambda: [1004, 416])
    monkeypatch.setattr(sys, "argv", ["layout", "pane"])
    layout.main()
    assert json.loads(capsys.readouterr().out)["android_fit"] is True


def test_pane_mode_reports_mismatch_when_android_is_taller(desktop, panel, monkeypatch, capsys):
    """Androidがペインより大きいと窓は縮まずはみ出すため、ぴったりではないと知らせる。"""
    monkeypatch.setattr(layout, "android_size", lambda: [1004, 440])
    monkeypatch.setattr(sys, "argv", ["layout", "pane"])
    layout.main()
    report = json.loads(capsys.readouterr().out)
    assert report["android_rect"] == [413, 12, 1004, 416]
    assert report["android_fit"] is False


def test_center_pane_rejects_too_short_screen():
    """枠の分を引くと高さが残らない画面は拒否する。"""
    with pytest.raises(ValueError):
        layout.center_pane(1920, 24)


def test_leaving_pane_mode_restores_panel(desktop, panel, monkeypatch):
    """paneモードで止めたパネルは、通常の配置へ戻すときに再開する。"""
    home, _, _ = desktop
    monkeypatch.setattr(layout, "android_size", lambda: None)
    monkeypatch.setattr(sys, "argv", ["layout", "pane"])
    layout.main()
    assert panel["started"] == 0
    monkeypatch.setattr(sys, "argv", ["layout", "restore"])
    layout.main()
    assert panel["started"] == 1 and panel["running"]
    assert json.loads((home / ".local/state/argos/window-layout.json").read_text())["panel_hidden"] is False


def test_panel_left_alone_when_not_hidden_by_tool(desktop, panel, monkeypatch):
    """このツールが止めていないパネルは、通常の配置でも触らない。"""
    panel["running"] = False
    monkeypatch.setattr(sys, "argv", ["layout", "split"])
    layout.main()
    assert panel["started"] == 0


def test_pane_mode_without_panel_binary(desktop, panel, monkeypatch):
    """パネルのない端末では、パネル操作をせずに配置する。"""
    monkeypatch.setattr(layout.shutil, "which", lambda tool: None if tool == "wf-panel-pi" else f"/usr/bin/{tool}")
    monkeypatch.setattr(layout, "android_size", lambda: None)
    monkeypatch.setattr(sys, "argv", ["layout", "pane", "--pane", "10,0,500,440"])
    layout.main()
    assert panel["killed"] == []


def test_boot_recovers_android_even_if_argos_is_late(desktop, android, monkeypatch):
    """ARGOSのウィンドウが出なくても、Androidの解凍・起動は先に済ませて失敗を返す。"""
    android.update(frozen=True, window=False, appear_after=1, argos=False)
    monkeypatch.setattr(layout, "BOOT_WAIT_SECONDS", 2)
    monkeypatch.setattr(sys, "argv", ["layout", "boot"])
    with pytest.raises(RuntimeError, match="ARGOS"):
        layout.main()
    assert android["launches"] == 1 and not android["frozen"]


def _run(monkeypatch, *argv):
    """コマンドを実行して保存後の状態を返す。"""
    monkeypatch.setattr(sys, "argv", ["layout", *argv])
    layout.main()
    return json.loads((layout.Path.home() / ".local/state/argos/window-layout.json").read_text())


def test_hide_and_show_return_to_previous_layout(desktop, panel, monkeypatch):
    """隠す前の配置を覚え、showでその配置へ戻る。二重にhideしても覚えたまま。"""
    monkeypatch.setattr(layout, "android_size", lambda: None)
    assert _run(monkeypatch, "pane")["mode"] == "pane"
    state = _run(monkeypatch, "hide")
    assert (state["mode"], state["return_mode"]) == ("argos", "pane")
    assert _run(monkeypatch, "hide")["return_mode"] == "pane"
    assert _run(monkeypatch, "show")["mode"] == "pane"


def test_argos_mode_also_remembers_previous_layout(desktop, monkeypatch):
    """argosモードへ切り替えたときも、直前の配置をshowで戻せる。"""
    assert _run(monkeypatch, "split")["mode"] == "split"
    assert _run(monkeypatch, "argos")["return_mode"] == "split"
    assert _run(monkeypatch, "show")["mode"] == "split"


def test_show_without_hide_keeps_current_layout(desktop, monkeypatch):
    """隠していないときのshowは、今の配置を再適用するだけ。"""
    assert _run(monkeypatch, "android")["mode"] == "android"
    assert _run(monkeypatch, "show")["mode"] == "android"


def test_show_defaults_to_split_when_nothing_remembered(desktop, monkeypatch):
    """戻し先が未記録のままARGOSだけの状態でshowすると、左右分割へ戻る。"""
    home, _, _ = desktop
    path = home / ".local/state/argos/window-layout.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mode": "argos"}))
    assert _run(monkeypatch, "show")["mode"] == "split"


def _randr(monkeypatch, outputs):
    """wlr-randr --jsonの出力を差し替える。"""
    monkeypatch.setattr(layout.subprocess, "check_output", lambda command, **kwargs: json.dumps(outputs))


def _output(width=1920, height=440, **extra):
    """現在のモードを1つ持つ出力を作る。"""
    return {"name": "HDMI-A-1", "enabled": True, "scale": 1.0, "transform": "normal", "modes": [{"width": width, "height": height, "current": True}], **extra}


@pytest.mark.parametrize("width, height", [(1920, 440), (1280, 720), (1366, 768)])
def test_detect_display_reads_current_mode(monkeypatch, width, height):
    """有効な1画面の現在のモードから、幅と高さを取得する。"""
    _randr(monkeypatch, [_output(width, height), {"name": "HDMI-A-2", "enabled": False, "modes": []}])
    assert layout.detect_display() == (width, height)


@pytest.mark.parametrize(
    "outputs",
    [
        [_output(), _output()],
        [],
        [_output(scale=2.0)],
        [_output(transform="90")],
        [{"enabled": True, "scale": 1.0, "transform": "normal", "modes": [{"width": 1, "height": 1}]}],
    ],
)
def test_detect_display_rejects_unsupported(monkeypatch, outputs):
    """複数画面・拡大・回転・現在のモード不明は、未対応として拒否する。"""
    _randr(monkeypatch, outputs)
    with pytest.raises(RuntimeError):
        layout.detect_display()


@pytest.mark.parametrize("error", [FileNotFoundError(), subprocess.CalledProcessError(1, "wlr-randr")])
def test_detect_display_command_failure(monkeypatch, error):
    """wlr-randrが使えない環境では、手動指定を案内するエラーにする。"""
    def fail(*args, **kwargs):
        """コマンドの失敗を模擬する。"""
        raise error

    monkeypatch.setattr(layout.subprocess, "check_output", fail)
    with pytest.raises(RuntimeError, match="--width"):
        layout.detect_display()


def test_detect_display_invalid_json(monkeypatch):
    """出力がJSONでなければ、手動指定を案内するエラーにする。"""
    monkeypatch.setattr(layout.subprocess, "check_output", lambda *args, **kwargs: "not json")
    with pytest.raises(RuntimeError):
        layout.detect_display()


def test_resolve_display(monkeypatch):
    """幅と高さの両方の指定が優先され、片方だけは拒否し、無指定なら自動取得する。"""
    monkeypatch.setattr(layout, "detect_display", lambda: (800, 600))
    assert layout.resolve_display(SimpleNamespace(width=1024, height=768)) == (1024, 768)
    assert layout.resolve_display(SimpleNamespace(width=None, height=None)) == (800, 600)
    with pytest.raises(RuntimeError):
        layout.resolve_display(SimpleNamespace(width=1024, height=None))


def test_geometry_leaves_room_for_panel():
    """上部のパネルの分だけ、左右の窓の上端と高さを詰める。"""
    android, argos = layout.geometry(1920, 440, 50, "left", top=36)
    assert android == (0, 36, 960, 404) and argos == (960, 36, 960, 404)


@pytest.mark.parametrize("width, height", [(1280, 720), (1366, 768)])
def test_layouts_follow_display_size(width, height):
    """画面サイズが違っても、中央ペインは画面内に収まり、分割は画面を覆う。"""
    x, y, w, h = layout.center_pane(width, height)
    assert x > 0 and x + w < width and y + h < height
    android, argos = layout.geometry(width, height, 50, "left", top=36)
    assert android[2] + argos[2] == width and android[3] == height - 36


def _config(tmp_path, monkeypatch, text=""):
    """設定ファイルだけを使う状態にする。"""
    config = tmp_path / "config.yaml"
    config.write_text(text)
    monkeypatch.setenv("ARGOS_CONFIG_FILE", str(config))
    for name in (layout.VIEW_SETTING, layout.STYLE_SETTING, layout.RATIO_SETTING, layout.PANEL_HEIGHT_SETTING, layout.RESTART_SERVICES_SETTING, layout.DASHBOARD_LAYOUT_SETTING, layout.SWAP_SETTING, "ARGOS_DASHBOARD_PORT", "ARGOS_DASHBOARD_TOKEN", "ARGOS_DASHBOARD_VIEW_KEY", "ARGOS_DASHBOARD_SSL", "ARGOS_DASHBOARD_SSL_CERT_PATH"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("style, mode", [("", "split"), ("split", "split"), ("overlay", "pane")])
def test_default_mode_follows_style(tmp_path, monkeypatch, style, mode):
    """設定の表示方式が、最初に使うモードになる。未設定は左右分割。"""
    _config(tmp_path, monkeypatch, f"window_layout:\n  style: '{style}'\n  split_ratio: 40\n")
    state = layout.load_state(tmp_path / "none.json")
    assert (state["mode"], state["ratio"]) == (mode, 40)


def test_default_mode_rejects_unknown_style(tmp_path, monkeypatch):
    """未知の表示方式は、黙って無視せず拒否する。"""
    _config(tmp_path, monkeypatch, "window_layout:\n  style: floating\n")
    with pytest.raises(RuntimeError, match="overlay"):
        layout.default_mode()


@pytest.mark.parametrize("value", ["abc", "10", "90"])
def test_split_ratio_setting_rejected_when_invalid(tmp_path, monkeypatch, value):
    """分割の比率が数値でない・範囲外なら拒否する。"""
    _config(tmp_path, monkeypatch, f"window_layout:\n  split_ratio: '{value}'\n")
    with pytest.raises(RuntimeError):
        layout.load_state(tmp_path / "none.json")


def test_saved_state_beats_config_defaults(tmp_path, monkeypatch):
    """保存済みのモードと比率は、設定の既定値より優先する。"""
    _config(tmp_path, monkeypatch, "window_layout:\n  style: overlay\n  split_ratio: 40\n")
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"mode": "android", "ratio": 70}))
    state = layout.load_state(path)
    assert (state["mode"], state["ratio"]) == ("android", 70)


def test_effective_panel_height(tmp_path, monkeypatch):
    """パネルがない端末では0、あれば設定値（既定36）を使う。"""
    _config(tmp_path, monkeypatch)
    monkeypatch.setattr(layout.shutil, "which", lambda tool: None)
    assert layout.effective_panel_height() == 0
    monkeypatch.setattr(layout.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    assert layout.effective_panel_height() == 36
    _config(tmp_path, monkeypatch, "window_layout:\n  panel_height: 48\n")
    assert layout.effective_panel_height() == 48


@pytest.mark.parametrize(
    "mode, expected_rect, expected_needed",
    [
        ("argos", None, None),
        ("android", [0, 0, 1920, 440], [1920, 440]),
        ("split", [0, 36, 960, 404], [960, 404]),
        ("pane", [413, 12, 1004, 416], [1004, 416]),
    ],
)
def test_plan_android_per_mode(mode, expected_rect, expected_needed):
    """モードごとに、Androidの窓の位置と、必要な描画サイズが決まる。"""
    state = {"mode": mode, "ratio": 50, "side": "left", "pane": None}
    assert layout.plan_android(state, 1920, 440, 36, None) == (expected_rect, expected_needed)


def test_plan_android_third_on_right_side():
    """比率33%を右側に置く3分割にも、同じ仕組みで対応する。"""
    state = {"mode": "split", "ratio": 33, "side": "right", "pane": None}
    rect, needed = layout.plan_android(state, 1920, 440, 36, None)
    assert rect == [1920 - 634, 36, 634, 404] and needed == [634, 404]


def test_split_binding_starts_below_panel():
    """分割の窓は、パネルの下から始まり、パネルの分だけ高さを詰める。"""
    root = ET.fromstring("<openbox_config><keyboard/></openbox_config>")
    state = {"mode": "split", "ratio": 50, "side": "left", "android_app": PACKAGE}
    result = layout.build_binding(root, state, 1920, 440, (), top=36)
    moves = [(node.get("x"), node.get("y")) for node in result.iter() if node.get("name") == "MoveTo"]
    resizes = [(node.get("width"), node.get("height")) for node in result.iter() if node.get("name") == "ResizeTo"]
    assert moves == [("0", "36"), ("960", "36")] and resizes == [("960", "404"), ("960", "404")]


def test_split_reports_restart_required_when_size_differs(desktop, monkeypatch, capsys):
    """Androidの描画サイズが必要なサイズと違えば、再起動が必要と知らせるだけで再起動しない。"""
    monkeypatch.setattr(layout, "android_size", lambda: [1004, 416])
    called = []
    monkeypatch.setattr(layout, "restart_android", lambda size: called.append(size))
    monkeypatch.setattr(sys, "argv", ["layout", "split"])
    layout.main()
    report = json.loads(capsys.readouterr().out)
    assert report["android_needed"] == [960, 404] and report["android_current"] == [1004, 416]
    assert report["restart_required"] is True and report["android_fit"] is False
    assert called == []


def test_restart_android_flag_restarts_then_lays_out(desktop, monkeypatch, capsys):
    """--restart-androidなら、必要なサイズへ再起動してから配置し、ぴったりだと知らせる。"""
    monkeypatch.setattr(layout, "android_size", lambda: [1004, 416])
    called = []
    monkeypatch.setattr(layout, "restart_android", lambda size: called.append(size))
    monkeypatch.setattr(sys, "argv", ["layout", "split", "--restart-android"])
    layout.main()
    report = json.loads(capsys.readouterr().out)
    assert called == [[960, 404]]
    assert report["restart_required"] is False and report["android_fit"] is True


def test_no_restart_when_size_matches_or_unknown(desktop, monkeypatch):
    """サイズが合っている、または取得できないときは、フラグがあっても再起動しない。"""
    called = []
    monkeypatch.setattr(layout, "restart_android", lambda size: called.append(size))
    for size in ([960, 404], None):
        monkeypatch.setattr(layout, "android_size", lambda size=size: size)
        monkeypatch.setattr(sys, "argv", ["layout", "split", "--restart-android"])
        layout.main()
    assert called == []


def test_display_flags_override_detection(desktop, monkeypatch, capsys):
    """--widthと--heightを指定すると、自動取得より優先する。"""
    monkeypatch.setattr(layout, "detect_display", lambda: pytest.fail("自動取得しないはず"))
    monkeypatch.setattr(sys, "argv", ["layout", "split", "--width", "1280", "--height", "720"])
    layout.main()
    assert json.loads(capsys.readouterr().out)["display"] == [1280, 720]


def test_restore_uses_configured_style(desktop, monkeypatch):
    """restoreは、設定の表示方式のモードへ戻す。"""
    monkeypatch.setenv(layout.STYLE_SETTING, "overlay")
    assert _run(monkeypatch, "split")["mode"] == "split"
    assert _run(monkeypatch, "restore")["mode"] == "pane"


@pytest.fixture
def restart(monkeypatch):
    """再起動手順で実行されるコマンドを記録し、成功する環境を模擬する。"""
    world = {"commands": [], "units": {"waydroid-session.service"}, "sudo_ok": True, "running_after": 2, "checks": 0, "popen": []}
    monkeypatch.setattr(layout.time, "sleep", lambda seconds: None)
    monkeypatch.setenv(layout.RESTART_SERVICES_SETTING, "waydroid-gps-bridge.service, other.service")

    def run(command, **kwargs):
        """コマンドを記録する。sudoは失敗も模擬できる。"""
        world["commands"].append(command)
        if command[0] == "sudo" and not world["sudo_ok"]:
            raise subprocess.CalledProcessError(1, command)
        if command[:3] == ["systemctl", "--user", "cat"]:
            return SimpleNamespace(returncode=0 if command[3] in world["units"] else 1)
        return SimpleNamespace(returncode=0)

    def status(command, **kwargs):
        """セッションが数回目の確認で立ち上がる様子を模擬する。"""
        world["checks"] += 1
        return "Session:\tRUNNING\n" if world["checks"] >= world["running_after"] else "Session:\tSTOPPED\n"

    monkeypatch.setattr(layout.subprocess, "run", run)
    monkeypatch.setattr(layout.subprocess, "check_output", status)
    monkeypatch.setattr(layout.subprocess, "Popen", lambda command, **kwargs: world["popen"].append(command))
    return world


def test_restart_android_sequence(restart):
    """サイズ設定→セッション停止→コンテナ再起動→セッション開始の順で行い、関連サービスを止めて再開する。"""
    layout.restart_android([960, 404])
    commands = restart["commands"]
    names = [" ".join(command[:3]) for command in commands]
    assert commands[0] == ["systemctl", "--user", "stop", "waydroid-gps-bridge.service"]
    assert ["waydroid", "prop", "set", "persist.waydroid.width", "960"] in commands
    assert ["waydroid", "prop", "set", "persist.waydroid.height", "404"] in commands
    order = [names.index(key) for key in ("waydroid session stop", "sudo -n systemctl", "systemctl --user start")]
    assert order == sorted(order)
    assert commands[-1] == ["systemctl", "--user", "start", "other.service"]
    assert restart["popen"] == []


def test_restart_android_starts_session_directly_without_unit(restart):
    """セッション用のユーザーサービスがない端末では、waydroid session startを直接起動する。"""
    restart["units"] = set()
    layout.restart_android([960, 404])
    assert restart["popen"] == [["waydroid", "session", "start"]]


def test_restart_android_requires_passwordless_sudo(restart):
    """パスワードなしのsudoがなければ、分かりやすいエラーにする。"""
    restart["sudo_ok"] = False
    with pytest.raises(RuntimeError, match="sudo"):
        layout.restart_android([960, 404])


def test_restart_android_times_out_when_session_stays_down(restart):
    """再起動後にセッションが立ち上がらなければ、成功扱いにしない。"""
    restart["running_after"] = 10**6
    with pytest.raises(RuntimeError, match="セッション"):
        layout.restart_android([960, 404], wait=3)


def test_session_running_handles_missing_command(monkeypatch):
    """waydroidコマンドがなければ、動いていない扱いにする。"""
    def fail(*args, **kwargs):
        """コマンドがない状態を模擬する。"""
        raise FileNotFoundError

    monkeypatch.setattr(layout.subprocess, "check_output", fail)
    assert layout.session_running() is False


@pytest.mark.parametrize("mode, expected", [("split", "sp"), ("pane", "standard"), ("argos", None), ("android", None)])
def test_desired_dashboard_layout_auto(tmp_path, monkeypatch, mode, expected):
    """左右分割はSP、重ねる表示は通常にし、それ以外のモードでは変えない。"""
    _config(tmp_path, monkeypatch)
    assert layout.desired_dashboard_layout(mode) == expected


@pytest.mark.parametrize("mode", ["split", "pane", "argos"])
def test_desired_dashboard_layout_fixed_setting(tmp_path, monkeypatch, mode):
    """設定でレイアウトを固定すると、モードによらずそれを使う。"""
    _config(tmp_path, monkeypatch, "window_layout:\n  dashboard_layout: grid\n")
    assert layout.desired_dashboard_layout(mode) == "grid"
    monkeypatch.setenv(layout.DASHBOARD_LAYOUT_SETTING, "auto")
    assert layout.desired_dashboard_layout(mode) == layout.AUTO_DASHBOARD_LAYOUT.get(mode)


def test_desired_dashboard_layout_rejects_unknown(monkeypatch):
    """未知のレイアウト名は拒否する。"""
    monkeypatch.setenv(layout.DASHBOARD_LAYOUT_SETTING, "tiles")
    with pytest.raises(RuntimeError, match="standard"):
        layout.desired_dashboard_layout("split")


def _kiosk_env(desktop, monkeypatch, exists=True):
    """キオスクの有無と再起動の記録を用意する。"""
    home, _, calls = desktop
    real_run = layout.subprocess.run

    def run(command, **kwargs):
        """キオスクunitの有無を切り替えられるようにする。"""
        if command[:4] == ["systemctl", "--user", "cat", layout.KIOSK_UNIT]:
            return SimpleNamespace(returncode=0 if exists else 1)
        return real_run(command, **kwargs)

    monkeypatch.setattr(layout.subprocess, "run", run)
    return home / ".local/state/argos/dashboard-layout", calls


def test_apply_dashboard_layout_restarts_only_on_change(desktop, monkeypatch):
    """レイアウトが変わったときだけ、ファイルを書いてキオスクを再起動する。"""
    path, calls = _kiosk_env(desktop, monkeypatch)
    restart = ["systemctl", "--user", "restart", layout.KIOSK_UNIT]
    assert layout.apply_dashboard_layout("split") is True
    assert path.read_text().strip() == "sp" and calls.count(restart) == 1
    assert layout.apply_dashboard_layout("split") is False
    assert layout.apply_dashboard_layout("pane") is True
    assert path.read_text().strip() == "standard" and calls.count(restart) == 2
    assert layout.apply_dashboard_layout("argos") is False
    assert path.read_text().strip() == "standard"


def test_apply_dashboard_layout_skips_without_kiosk(desktop, monkeypatch):
    """キオスクをこのユーザーが動かしていない端末では、何もしない。"""
    path, calls = _kiosk_env(desktop, monkeypatch, exists=False)
    assert layout.apply_dashboard_layout("split") is False
    assert not path.exists()
    assert ["systemctl", "--user", "restart", layout.KIOSK_UNIT] not in calls


def test_layout_switches_with_style(desktop, monkeypatch, capsys):
    """分割ではSP、重ねる表示では通常へ切り替わり、同じ表示方式の再実行では再起動しない。"""
    path, calls = _kiosk_env(desktop, monkeypatch)
    for mode, layout_name, restarted in (("split", "sp", True), ("split", "sp", False), ("pane", "standard", True), ("pane", "standard", False)):
        monkeypatch.setattr(sys, "argv", ["layout", mode])
        layout.main()
        report = json.loads(capsys.readouterr().out)
        assert (report["dashboard_layout"], report["dashboard_restarted"]) == (layout_name, restarted)
        assert path.read_text().strip() == layout_name


def test_hide_keeps_dashboard_layout(desktop, monkeypatch, capsys):
    """ARGOSだけの表示に切り替えても、ダッシュボードのレイアウトは変えず、キオスクも再起動しない。"""
    path, calls = _kiosk_env(desktop, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["layout", "split"])
    layout.main()
    capsys.readouterr()
    calls.clear()
    monkeypatch.setattr(sys, "argv", ["layout", "hide"])
    layout.main()
    assert json.loads(capsys.readouterr().out)["dashboard_restarted"] is False
    assert path.read_text().strip() == "sp"
    assert ["systemctl", "--user", "restart", layout.KIOSK_UNIT] not in calls


def test_layout_fails_if_argos_window_missing_after_kiosk_restart(desktop, monkeypatch):
    """キオスクを再起動したのにARGOSの窓が出なければ、成功扱いにしない。"""
    monkeypatch.setattr(layout, "apply_dashboard_layout", lambda mode: True)
    monkeypatch.setattr(layout, "wait_window", lambda match, seconds: False)
    monkeypatch.setattr(sys, "argv", ["layout", "split"])
    with pytest.raises(RuntimeError, match="レイアウト切り替え後"):
        layout.main()


def test_lock_waits_for_other_operation(desktop, monkeypatch):
    """別の配置操作がロックを持っている間は待ち、待ち切れなければ分かりやすいエラーにする。"""
    home, config, _ = desktop
    lock_path = home / ".local/state/argos/window-layout.lock"
    lock_path.parent.mkdir(parents=True)
    original = config.read_bytes()
    monkeypatch.setattr(layout, "LOCK_WAIT_SECONDS", 2)
    with lock_path.open("w") as other:
        layout.fcntl.flock(other, layout.fcntl.LOCK_EX)
        monkeypatch.setattr(sys, "argv", ["layout", "split"])
        with pytest.raises(RuntimeError, match="別の画面配置"):
            layout.main()
    assert config.read_bytes() == original
    layout.main()


@pytest.fixture
def dashboard(desktop, monkeypatch):
    """ダッシュボードのスロット状態と呼び出しを模擬する。中央ペインの先頭の種類は書き換えられる。"""
    world = {"center": "conversation", "calls": [], "down": False}

    def call(path, payload=None):
        """状態の取得と入れ替えを記録し、入れ替えると中央の種類も入れ替わる。"""
        world["calls"].append((path, payload))
        if world["down"]:
            raise OSError("接続できません")
        if payload:
            world["center"] = "notifications" if world["center"] == "conversation" else "conversation"
            return {"status": "slots_swapped"}
        right = "notifications" if world["center"] == "conversation" else "conversation"
        return {"slot_stacks": {"center": [{"type": "notifications"}, {"type": world["center"]}], "right": [{"type": right}]}}

    monkeypatch.setattr(layout, "dashboard_call", call)
    return world


def test_pane_moves_conversation_to_right(dashboard, monkeypatch, capsys):
    """オーバーレイ(pane)に入るとき、会話欄が中央にあれば入れ替えて右へ移す。"""
    monkeypatch.setattr(sys, "argv", ["layout", "pane"])
    layout.main()
    assert json.loads(capsys.readouterr().out)["conversation"] == "swapped"
    assert dashboard["calls"][-1] == ("/api/events", {"type": "swap_slots"})
    layout.main()
    assert json.loads(capsys.readouterr().out)["conversation"] == "already"
    assert sum(1 for call in dashboard["calls"] if call[1]) == 1


def test_pane_leaves_slots_when_center_is_not_conversation(dashboard, monkeypatch):
    """中央が通知欄などで会話欄でなければ、入れ替えない（もう一度入れ替えると会話欄が隠れる）。"""
    dashboard["center"] = "notifications"
    monkeypatch.setattr(sys, "argv", ["layout", "pane"])
    layout.main()
    assert not any(call[1] for call in dashboard["calls"])


@pytest.mark.parametrize("mode", ["split", "hide", "android", "argos"])
def test_other_modes_do_not_touch_dashboard_slots(dashboard, monkeypatch, capsys, mode):
    """オーバーレイ以外のモードでは、ダッシュボードのスロットに触れない。"""
    monkeypatch.setattr(sys, "argv", ["layout", mode])
    layout.main()
    assert "conversation" not in json.loads(capsys.readouterr().out)
    assert dashboard["calls"] == []


def test_pane_continues_when_dashboard_is_down(dashboard, monkeypatch, capsys):
    """ダッシュボードが応答しなくても、配置は成功として、入れ替えを見送ったと知らせる。"""
    dashboard["down"] = True
    monkeypatch.setattr(sys, "argv", ["layout", "pane"])
    layout.main()
    report = json.loads(capsys.readouterr().out)
    assert report["conversation"].startswith("skipped") and report["mode"] == "pane"


@pytest.mark.parametrize("value", ["false", "no", "0", "OFF"])
def test_swap_can_be_disabled(dashboard, monkeypatch, capsys, value):
    """設定swap_conversationをfalseにすると、自動で入れ替えない。"""
    monkeypatch.setenv(layout.SWAP_SETTING, value)
    monkeypatch.setattr(sys, "argv", ["layout", "pane"])
    layout.main()
    assert json.loads(capsys.readouterr().out)["conversation"] == "disabled"
    assert dashboard["calls"] == []


class _Response:
    """urlopenの戻り値を模擬する。"""

    def __init__(self, body):
        """レスポンスの本文を保持する。"""
        self.body = body

    def __enter__(self):
        """with文で自身を返す。"""
        return self

    def __exit__(self, *args):
        """with文の終了処理。何もしない。"""

    def read(self):
        """本文を返す。"""
        return self.body


def test_dashboard_call_get_uses_view_key_and_port(tmp_path, monkeypatch):
    """状態の取得は、設定のポートと閲覧キー付きのGETで行う。"""
    _config(tmp_path, monkeypatch, "dashboard:\n  port: 9000\n  view_key: viewer\n")
    seen = []
    monkeypatch.setattr(layout.urllib.request, "urlopen", lambda request, timeout, context: seen.append((request, context)) or _Response(b'{"ok": 1}'))
    assert layout.dashboard_call("/api/state") == {"ok": 1}
    request, context = seen[0]
    assert request.full_url == "http://127.0.0.1:9000/api/state?key=viewer" and request.get_method() == "GET"
    assert context is None


def test_dashboard_call_post_sends_bearer_token(tmp_path, monkeypatch):
    """更新はBearer認証付きのPOSTで送る。"""
    _config(tmp_path, monkeypatch, "dashboard:\n  token: secret\n")
    seen = []
    monkeypatch.setattr(layout.urllib.request, "urlopen", lambda request, timeout, context: seen.append((request, context)) or _Response(b'{"status": "slots_swapped"}'))
    assert layout.dashboard_call("/api/events", {"type": "swap_slots"})["status"] == "slots_swapped"
    request, _ = seen[0]
    assert request.get_method() == "POST" and request.get_header("Authorization") == "Bearer secret"
    assert json.loads(request.data) == {"type": "swap_slots"}
    assert request.full_url == "http://127.0.0.1:8765/api/events"


def test_dashboard_call_post_requires_token(tmp_path, monkeypatch):
    """トークンが未設定なら、送らずにエラーにする。"""
    _config(tmp_path, monkeypatch)
    monkeypatch.delenv("ARGOS_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr(layout.urllib.request, "urlopen", lambda *args, **kwargs: pytest.fail("送信しないはず"))
    with pytest.raises(RuntimeError, match="TOKEN"):
        layout.dashboard_call("/api/events", {"type": "swap_slots"})


def test_dashboard_call_uses_https_with_trusted_certificate(tmp_path, monkeypatch):
    """HTTPSが有効なら、設定した自己署名証明書を信頼するhttps接続にする。"""
    certificate = tmp_path / "dashboard.crt"
    certificate.write_text("dummy")
    _config(tmp_path, monkeypatch, f"dashboard:\n  ssl: true\n  ssl_cert_path: {certificate}\n")
    seen = []
    contexts = []
    monkeypatch.setattr(layout.ssl, "create_default_context", lambda cafile: contexts.append(cafile) or "context")
    monkeypatch.setattr(layout.urllib.request, "urlopen", lambda request, timeout, context: seen.append((request, context)) or _Response(b"{}"))
    layout.dashboard_call("/api/state")
    request, context = seen[0]
    assert request.full_url.startswith("https://127.0.0.1:8765/api/state") and context == "context"
    assert contexts == [certificate]


def test_dashboard_call_https_requires_certificate(tmp_path, monkeypatch):
    """HTTPSなのに証明書がなければ、接続せずエラーにする。"""
    _config(tmp_path, monkeypatch, f"dashboard:\n  ssl: true\n  ssl_cert_path: {tmp_path / 'missing.crt'}\n")
    monkeypatch.setattr(layout.urllib.request, "urlopen", lambda *args, **kwargs: pytest.fail("接続しないはず"))
    with pytest.raises(RuntimeError, match="証明書"):
        layout.dashboard_call("/api/state")


@pytest.mark.parametrize("value, view", [("", "app"), ("app", "app"), ("FULL", "full"), ("full", "full")])
def test_android_view_setting(monkeypatch, value, view):
    """Androidの見せ方は、app（アプリごとのウィンドウ）とfull（Android全体で1つ）から選ぶ。未設定はapp。"""
    monkeypatch.setenv(layout.VIEW_SETTING, value)
    assert layout.android_view() == view


def test_android_view_rejects_unknown_value(monkeypatch):
    """不明な値は、黙って無視せず、エラーにする。"""
    monkeypatch.setenv(layout.VIEW_SETTING, "multi")
    with pytest.raises(RuntimeError, match="app/full"):
        layout.android_view()


def test_android_id_depends_on_view(monkeypatch):
    """appならアプリごとのapp_id、fullなら、アプリによらず、Android全体の1つのapp_idになる。"""
    monkeypatch.setenv(layout.VIEW_SETTING, "app")
    assert layout.android_id(PACKAGE) == f"waydroid.{PACKAGE}"
    monkeypatch.setenv(layout.VIEW_SETTING, "full")
    assert layout.android_id(PACKAGE) == "Waydroid"


def test_binding_targets_the_full_ui_window(monkeypatch):
    """full表示のときは、labwcの操作の対象も、Android全体のウィンドウになる。"""
    monkeypatch.setenv(layout.VIEW_SETTING, "full")
    root = ET.fromstring("<openbox_config><keyboard/></openbox_config>")
    result = layout.build_binding(root, {"mode": "split", "ratio": 53, "side": "right", "android_app": PACKAGE}, 1920, 440, (), top=36)
    identifiers = [node.get("identifier") for node in result.iter() if str(node.tag).endswith("query")]
    assert "Waydroid" in identifiers and f"waydroid.{PACKAGE}" not in identifiers


def test_ensure_android_full_view_shows_full_ui_and_starts_the_app(android, monkeypatch):
    """full表示では、アプリ起動ではなく、Android全体のウィンドウを出し、出たあとでアプリを前面に出す。"""
    monkeypatch.setenv(layout.VIEW_SETTING, "full")
    android.update(window=False, appear_after=1)
    started = []
    original = layout.subprocess.run

    def run(command, **kwargs):
        """起動コマンドの種類を記録し、full表示ではウィンドウが現れる状態にする。"""
        if command[:2] == ["waydroid", "show-full-ui"]:
            android["window"] = True
            started.append("show-full-ui")
            return SimpleNamespace(returncode=0)
        if command[:2] == ["waydroid", "app"]:
            started.append("app-launch")
        if command[: len(layout.LXC_ATTACH)] == layout.LXC_ATTACH:
            started.append(command[len(layout.LXC_ATTACH) :][:1] + [command[-1]])
            return SimpleNamespace(returncode=0)
        return original(command, **kwargs)

    monkeypatch.setattr(layout.subprocess, "run", run)
    layout.ensure_android(PACKAGE)
    assert started == ["show-full-ui", ["am", PACKAGE]]


def test_ensure_android_full_view_skips_when_window_is_shown(android, monkeypatch):
    """full表示で、ウィンドウがあり、凍結もしていなければ、何も起動しない。"""
    monkeypatch.setenv(layout.VIEW_SETTING, "full")
    layout.ensure_android(PACKAGE)
    assert android["launches"] == 0
