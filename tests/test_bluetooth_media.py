"""Bluetoothメディアボタン入力のテスト。"""

import threading

from argos.hardware import bluetooth_media


def _input():
    """実スレッドなしで入力ハンドラを作る。"""
    events = []
    device = bluetooth_media.BluetoothMediaInput.__new__(bluetooth_media.BluetoothMediaInput)
    device._pressed = False
    device._on_press = lambda: events.append("press")
    device._on_release = lambda: events.append("release")
    device._on_next = lambda: events.append("next")
    device._on_previous = lambda: events.append("previous")
    device._on_cancel = lambda: events.append("cancel")
    device._shutdown = threading.Event()
    device._mac = "DC:2C:26:08:75:08"
    device._on_connection_change = lambda _connected: None
    return device, events


def test_find_media_device_by_bluetooth_address(tmp_path):
    """イベント番号が変わっても機器固有アドレスで見つける。"""
    other = tmp_path / "event3" / "device"
    other.mkdir(parents=True)
    (other / "uniq").write_text("aa:bb:cc:dd:ee:ff")
    target = tmp_path / "event9" / "device"
    target.mkdir(parents=True)
    (target / "uniq").write_text("dc:2c:26:08:75:08\n")

    assert bluetooth_media.find_media_device("DC:2C:26:08:75:08", tmp_path).name == "event9"
    assert bluetooth_media.find_media_device("missing", tmp_path) is None


def test_media_keys_handle_press_release_and_ignore_repeat():
    """再生の押下・解放と曲送りを扱い、長押しの反復は無視する。"""
    device, events = _input()
    for code, value in ((164, 1), (164, 2), (164, 1), (163, 1), (163, 2), (165, 1), (164, 0), (164, 0)):
        device._handle_key(code, value)

    assert events == ["press", "next", "previous", "release"]


def test_read_device_decodes_events_and_does_not_handle_volume(monkeypatch, tmp_path):
    """Linux入力イベントを読み、音量キーはOSへ任せる。"""
    device, events = _input()
    data = b"".join(
        bluetooth_media._EVENT.pack(0, 0, 1, code, value)
        for code, value in ((164, 1), (115, 1), (164, 0))
    )
    path = tmp_path / "events"
    path.write_bytes(data)
    device._on_release = lambda: (events.append("release"), device._shutdown.set())

    device._read_device(path)

    assert events == ["press", "release"]


def test_read_device_cancels_recording_on_disconnect(tmp_path):
    """押下中に接続が切れたら録音を破棄する。"""
    device, events = _input()
    path = tmp_path / "events"
    path.write_bytes(bluetooth_media._EVENT.pack(0, 0, 1, 164, 1))

    device._read_device(path)

    assert events == ["press", "cancel"]


def test_read_device_reports_connection_until_input_disappears(tmp_path):
    """入力を開けた間だけ接続中として通知する。"""
    device, _ = _input()
    states = []
    device._on_connection_change = states.append
    path = tmp_path / "events"
    path.write_bytes(b"")

    device._read_device(path)

    assert states == [True, False]


def test_monitor_retries_after_read_error(monkeypatch):
    """読み取りエラー後も再接続待ちに戻る。"""
    device, _ = _input()
    monkeypatch.setattr(bluetooth_media, "find_media_device", lambda _mac: "/dev/input/event9")

    def fail(_path):
        """一度失敗して監視を終える。"""
        device._shutdown.set()
        raise OSError("切断")

    device._read_device = fail
    device._run()
