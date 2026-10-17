"""接続中Bluetooth機器の種別取得と表示状態のテスト。"""

import subprocess

from argos.services.bluetooth_devices import classify_bluetooth_icon, read_connected_bluetooth_types
from argos.services.dashboard.state import DashboardState


def test_classify_bluetooth_icon():
    """BlueZの代表的なアイコン名を画面用の種別へ変換する。"""
    assert classify_bluetooth_icon("input-keyboard") == "keyboard"
    assert classify_bluetooth_icon("input-mouse") == "mouse"
    assert classify_bluetooth_icon("audio-headset") == "headphones"
    assert classify_bluetooth_icon("audio-card") == "speaker"
    assert classify_bluetooth_icon("unknown") == "bluetooth"


def test_read_connected_bluetooth_types(monkeypatch):
    """接続済み機器だけを調べ、同じ種類は一度だけ返す。"""
    commands = []

    def fake_run(command, **kwargs):
        """bluetoothctlの応答を模擬する。"""
        commands.append(command)
        if command == ["bluetoothctl", "devices", "Connected"]:
            return subprocess.CompletedProcess(command, 0, "Device AA:BB:CC:DD:EE:01 Button\nDevice AA:BB:CC:DD:EE:02 Keyboard\nDevice AA:BB:CC:DD:EE:03 Speaker\n", "")
        if command[-1].endswith("03"):
            return subprocess.CompletedProcess(command, 0, "\tIcon: audio-card\n", "")
        return subprocess.CompletedProcess(command, 0, "\tIcon: input-keyboard\n", "")

    monkeypatch.setattr("argos.services.bluetooth_devices.subprocess.run", fake_run)
    assert read_connected_bluetooth_types() == ["keyboard", "speaker"]
    assert len(commands) == 4


def test_read_connected_bluetooth_types_handles_unavailable_bluez(monkeypatch):
    """BlueZが無い環境や応答しない環境では表示を消す。"""
    def unavailable(*args, **kwargs):
        """起動できないbluetoothctlを模擬する。"""
        raise FileNotFoundError

    monkeypatch.setattr("argos.services.bluetooth_devices.subprocess.run", unavailable)
    assert read_connected_bluetooth_types() == []


def test_dashboard_bluetooth_types_update_only_on_change():
    """状態には機器名やMACを含めず、切断時は一覧を空にする。"""
    state = DashboardState()
    revision = state.snapshot()["revision"]
    state.set_bluetooth_devices(["keyboard", "keyboard", "speaker", "invalid"])
    snapshot = state.snapshot()
    assert snapshot["bluetooth_devices"] == ["keyboard", "speaker"]
    assert snapshot["revision"] == revision + 1
    state.set_bluetooth_devices(["keyboard", "speaker"])
    assert state.snapshot()["revision"] == revision + 1
    state.set_bluetooth_devices([])
    assert state.snapshot()["bluetooth_devices"] == []
