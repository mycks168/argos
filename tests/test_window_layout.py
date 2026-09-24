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
    monkeypatch.setattr(layout.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(layout.subprocess, "check_output", lambda *args, **kwargs: "123\n")
    monkeypatch.setattr(layout.time, "sleep", lambda seconds: None)
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


def test_state_defaults(tmp_path):
    """旧形式の保存ファイルの余分な項目は無視し、不足は既定値で補う。"""
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
        def no_labwc(*args, **kwargs):
            """labwcのプロセスがない状態を模擬する。"""
            raise subprocess.CalledProcessError(1, "pgrep")

        monkeypatch.setattr(layout.subprocess, "check_output", no_labwc)
    original = config.read_bytes()
    monkeypatch.setattr(sys, "argv", ["layout", "boot"])
    layout.main()
    assert "skipped" in capsys.readouterr().out
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
