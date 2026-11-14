"""USBメディアノブ入力のテスト。"""

import struct
import threading

from argos.hardware import usb_knob


class Clock:
    """テストで進める時計。"""

    def __init__(self) -> None:
        """0秒から始める。"""
        self.now = 0.0

    def __call__(self) -> float:
        """現在時刻を返す。"""
        return self.now


def _input(clock=None):
    """実スレッドなしでノブ入力を作り、呼ばれた操作を記録する。"""
    events = []
    knob = usb_knob.UsbKnobInput(
        "514c:8850",
        on_toggle=lambda: events.append("toggle"),
        on_volume=lambda delta: events.append(("volume", delta)),
        on_next=lambda: events.append("next"),
        on_previous=lambda: events.append("previous"),
        on_disconnect=lambda: events.append("disconnect"),
        shutdown=threading.Event(),
        clock=clock or Clock(),
        start=False,
    )
    return knob, events


def _capabilities(*codes: int) -> str:
    """sysfs形式のキー対応ビット列を作る。"""
    bits = struct.calcsize("l") * 8
    words = [0] * (max(codes) // bits + 1)
    for code in codes:
        words[code // bits] |= 1 << (code % bits)
    return " ".join(f"{word:x}" for word in reversed(words))


def _event(root, name, vendor, product, capabilities):
    """sysfsのイベントデバイスを模して作る。"""
    device = root / name / "device"
    (device / "id").mkdir(parents=True)
    (device / "capabilities").mkdir()
    (device / "id/vendor").write_text(vendor + "\n")
    (device / "id/product").write_text(product + "\n")
    (device / "capabilities/key").write_text(capabilities + "\n")


def test_parse_device_id():
    """大文字や前ゼロ省略を正規化し、不正な形式は無視する。"""
    assert usb_knob.parse_device_id("514C:8850") == ("514c", "8850")
    assert usb_knob.parse_device_id(" 1:2 ") == ("0001", "0002")
    assert usb_knob.parse_device_id("514c") is None
    assert usb_knob.parse_device_id(":8850") is None


def test_has_key_reads_multiword_bitmap():
    """複数ワードにまたがるビット列から、キーの有無を読む。"""
    capabilities = _capabilities(usb_knob.KEY_MUTE, usb_knob.KEY_NEXTSONG)
    assert usb_knob.has_key(capabilities, usb_knob.KEY_MUTE)
    assert usb_knob.has_key(capabilities, usb_knob.KEY_NEXTSONG)
    assert not usb_knob.has_key(capabilities, usb_knob.KEY_VOLUMEUP)
    assert not usb_knob.has_key(capabilities, 4000)
    assert not usb_knob.has_key("zz", 1)


def test_find_knob_device_selects_keyboard_side(tmp_path):
    """同じ機器のマウス側ではなく、ミュートキーを持つキー側を選ぶ。"""
    _event(tmp_path, "event3", "1234", "5678", _capabilities(usb_knob.KEY_MUTE))
    _event(tmp_path, "event8", "514c", "8850", _capabilities(usb_knob.KEY_MUTE, usb_knob.KEY_VOLUMEUP))
    _event(tmp_path, "event9", "514c", "8850", _capabilities(272))
    (tmp_path / "event10").mkdir()

    assert usb_knob.find_knob_device("514C:8850", tmp_path).name == "event8"
    assert usb_knob.find_knob_device("dead:beef", tmp_path) is None
    assert usb_knob.find_knob_device("bad", tmp_path) is None


def test_toggle_uses_press_only_and_debounces():
    """押下と解放がまとめて届いても1回だけ切り替え、すぐ後の重複押下は無視する。"""
    clock = Clock()
    knob, events = _input(clock)
    knob._handle_key(usb_knob.KEY_MUTE, 1)
    knob._handle_key(usb_knob.KEY_MUTE, 0)
    clock.now = 0.1
    knob._handle_key(usb_knob.KEY_MUTE, 1)
    clock.now = 2.0
    knob._handle_key(usb_knob.KEY_MUTE, 1)
    knob._handle_key(usb_knob.KEY_MUTE, 2)

    assert events == ["toggle", "toggle"]


def test_volume_keys_change_by_step():
    """回転の1クリックごとに音量を上げ下げする。"""
    knob, events = _input()
    knob._handle_key(usb_knob.KEY_VOLUMEUP, 1)
    knob._handle_key(usb_knob.KEY_VOLUMEUP, 0)
    knob._handle_key(usb_knob.KEY_VOLUMEDOWN, 1)

    assert events == [("volume", usb_knob.VOLUME_STEP), ("volume", -usb_knob.VOLUME_STEP)]


def test_fast_rotation_switches_only_one_slot():
    """勢いよく回しても1つだけ切り替え、回転が止まってから次を受け付ける。"""
    clock = Clock()
    knob, events = _input(clock)
    for now in (0.0, 0.2, 0.4, 0.6, 0.9, 1.2):
        clock.now = now
        knob._handle_key(usb_knob.KEY_NEXTSONG, 1)
    clock.now = 1.5
    knob._handle_key(usb_knob.KEY_PREVIOUSSONG, 1)
    clock.now = 2.5
    knob._handle_key(usb_knob.KEY_PREVIOUSSONG, 1)
    clock.now = 3.5
    knob._handle_key(usb_knob.KEY_NEXTSONG, 1)

    assert events == ["next", "previous", "next"]


def test_read_device_decodes_events_and_reports_disconnect(tmp_path):
    """Linux入力イベントを読み、キー以外は無視し、読み終えたら切断を知らせる。"""
    knob, events = _input()
    data = b"".join(
        usb_knob._EVENT.pack(0, 0, kind, code, value)
        for kind, code, value in ((2, 8, 1), (1, usb_knob.KEY_VOLUMEUP, 1), (0, 0, 0), (1, usb_knob.KEY_MUTE, 1))
    )
    path = tmp_path / "events"
    path.write_bytes(data)

    knob._read_device(path)

    assert events == [("volume", usb_knob.VOLUME_STEP), "toggle", "disconnect"]


def test_run_retries_until_shutdown(monkeypatch):
    """機器がなければ探し直し、読み取り中の例外でも止まらない。"""
    knob, _ = _input()
    calls = []

    def fake_find(device_id):
        calls.append(device_id)
        if len(calls) >= 2:
            knob._shutdown.set()
        return usb_knob.Path("/nonexistent/event99")

    monkeypatch.setattr(usb_knob, "find_knob_device", fake_find)
    knob._run()

    assert calls == ["514c:8850", "514c:8850"]
