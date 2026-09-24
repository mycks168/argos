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
    assert layout.load_state(path) == {"ratio": 30, "side": "left", "mode": "split"}


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
