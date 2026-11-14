"""製品ID別の共通入力設定とLinux入力監視を検証する。"""

import json
import os
import threading
from pathlib import Path

import pytest

from argos.hardware import input_profiles as m


def profile(**changes):
    """既定ノブの設定を必要な項目だけ変えて作る。"""
    return m.parse_profiles(json.dumps([{"id": "514C:8850", "preset": "media_knob", **changes}]))[0]


def reader(config=None):
    """監視スレッドなしで共通入力と操作記録を作る。"""
    events = []
    clock = [0.0]
    stop = threading.Event()
    obj = m.ProfileInput(config or profile(), lambda *args: events.append(args),
                         lambda: events.append("disconnect"), stop, clock=lambda: clock[0], start=False)
    return obj, events, clock, stop


def pulse(obj, code, path=Path("event1")):
    """1回のキー押下と解放を送る。"""
    obj.handle_key(path, code, 1)
    obj.handle_key(path, code, 0)


def test_defaults_and_overrides():
    """製品IDを正規化し、別表記でも個別割り当てを優先する。"""
    p = profile(bindings={"115": {"action": "volume_up", "amount": 3}, "KEY_NEXTSONG": "none"})
    assert p.id == "514c:8850" and not p.exclusive
    assert p.bindings[115].amount == 3 and p.bindings[163].action == "none"
    assert m.product_id(" 1:2 ") == "0001:0002"
    assert m.key_code("KEY_F13") == 183 and m.key_code("KEY_F24") == 194
    assert m.key_code("KEY_F1") == 59 and m.key_code(30) == 30
    assert m.parse_profiles("[]") == ()


@pytest.mark.parametrize("raw", [
    "bad", "{}", "[null]", '[{"id":"bad"}]', '[{"id":"1:2","preset":"bad"}]',
    '[{"id":"1:2","bindings":[]}]', '[{"id":"1:2","exclusive":"true"}]',
    '[{"id":"1:2","preset":"media_ptt","extra":1}]',
    '[{"id":"1:2","preset":"media_ptt"},{"id":"0001:0002","preset":"media_knob"}]',
    '[{"id":"1:2","bindings":{"KEY_UNKNOWN":"cancel"}}]',
    '[{"id":"1:2","bindings":{"999":"cancel"}}]',
    '[{"id":"1:2","bindings":{"113":"unknown"}}]',
    '[{"id":"1:2","bindings":{"113":{"action":"cancel","extra":0}}}]',
    '[{"id":"1:2","bindings":{"113":{"action":"cancel","amount":true}}}]',
    '[{"id":"1:2","bindings":{"113":{"action":"cancel","amount":0}}}]',
    '[{"id":"1:2","bindings":{"113":{"action":"cancel","quiet_seconds":-1}}}}]',
    '[{"id":"1:2","bindings":{"113":{"action":"cancel","quiet_seconds":"NaN"}}}]',
    '[{"id":"1:2","bindings":{"113":{"action":"cancel","quiet_seconds":null}}}]',
    '[{"id":"1:2","bindings":{"113":{"action":"ptt","debounce_seconds":1}}}]',
    '[{"id":"1:2","bindings":{"113":"none"}}]',
])
def test_invalid_config(raw):
    """不正なID、操作、型、重複を黙って使わない。"""
    with pytest.raises(ValueError):
        m.parse_profiles(raw)


def test_hold_repeat_and_combined_nodes():
    """複合キーボードの複数PTTキーでも、最後の解放まで録音を保つ。"""
    obj, events, _, _ = reader(profile(preset="", bindings={"KEY_F13": "ptt", "KEY_F14": "ptt"}))
    a, b = Path("event1"), Path("event2")
    obj.handle_key(a, 183, 0)
    obj.handle_key(a, 183, 1)
    obj.handle_key(a, 183, 1)
    obj.handle_key(a, 183, 2)
    obj.handle_key(b, 184, 1)
    obj.handle_key(a, 183, 0)
    assert events == [("ptt", 1, 5)]
    obj.handle_key(b, 184, 0)
    assert events[-1] == ("ptt", 0, 5)
    pulse(obj, 30)


def test_debounce_and_rotation():
    """押し込みの重複と回転の連続切替を、設定値どおり抑える。"""
    obj, events, clock, _ = reader()
    pulse(obj, 113)
    clock[0] = 0.1
    pulse(obj, 113)
    clock[0] = 0.4
    pulse(obj, 113)
    pulse(obj, 163)
    clock[0] = 0.5
    pulse(obj, 165)
    clock[0] = 1.4
    pulse(obj, 165)
    pulse(obj, 114)
    assert [e[0] for e in events] == ["record_toggle", "record_toggle", "slot_next", "slot_previous", "volume_down"]


def test_device_matching_and_capabilities(tmp_path):
    """製品IDと対応キーで選び、マウス側や別のキーボードを除く。"""
    for name, identity, code in [("event1", "514c:8850", 113), ("event2", "514c:8850", 272), ("event3", "1:2", 113)]:
        device = tmp_path / name / "device"
        (device / "id").mkdir(parents=True)
        (device / "capabilities").mkdir()
        vendor, product = identity.split(":")
        (device / "id/vendor").write_text(vendor)
        (device / "id/product").write_text(product)
        bits = m.struct.calcsize("l") * 8
        words = [0] * (code // bits + 1)
        words[code // bits] = 1 << (code % bits)
        (device / "capabilities/key").write_text(" ".join(f"{word:x}" for word in reversed(words)))
    (tmp_path / "event4").mkdir()
    assert m.find_devices(profile(), tmp_path) == [Path("/dev/input/event1")]
    assert not m.matches_device(Path("event4"), (profile(),), tmp_path)
    (tmp_path / "event1/device/capabilities/key").write_text("bad value")
    assert m.find_devices(profile(), tmp_path) == []


@pytest.mark.parametrize("exclusive", [False, True])
def test_monitor_reads_partial_events_and_closes(monkeypatch, exclusive):
    """短い読み取りも復元し、専用機器だけ独占して終了時に解放する。"""
    obj, events, _, stop = reader(profile(exclusive=exclusive))
    path = Path("/dev/input/event1")
    calls = []
    chunks = iter([m.EVENT.pack(0, 0, 1, 113, 1)[:7], m.EVENT.pack(0, 0, 1, 113, 1)[7:], b""])
    monkeypatch.setattr(m, "find_devices", lambda _p: [path])
    monkeypatch.setattr(m.os, "open", lambda *_: 5)
    monkeypatch.setattr(m.fcntl, "ioctl", lambda *args: calls.append(args))
    monkeypatch.setattr(m.select, "select", lambda *_: ([5], [], []))
    monkeypatch.setattr(m.os, "read", lambda *_: next(chunks))
    monkeypatch.setattr(m.os, "close", lambda fd: (calls.append(("close", fd)), stop.set()))
    obj._run()
    assert events == [("record_toggle", 1, 5), "disconnect"]
    assert ((5, m.EVIOCGRAB, 1) in calls) == exclusive
    assert ("close", 5) in calls


@pytest.mark.parametrize("failure", ["open", "grab", "read", "select", "syn", "removed", "shutdown"])
def test_monitor_failure_and_shutdown(monkeypatch, failure):
    """開けない機器、切断、イベント欠落、終了でも再試行・解放を保証する。"""
    obj, events, _, stop = reader(profile(exclusive=failure == "grab"))
    path = Path("/dev/input/event1")
    scans = [0]

    def scan(_profile):
        """切断と終了を走査の進行で再現する。"""
        scans[0] += 1
        if scans[0] == 2:
            stop.set()
        return [] if failure == "removed" and scans[0] == 2 else [path]

    def fail(*_args):
        """I/Oエラー後に終了させる。"""
        stop.set()
        raise OSError("テスト用の切断")

    monkeypatch.setattr(m, "find_devices", scan)
    monkeypatch.setattr(m.os, "open", fail if failure == "open" else lambda *_: 5)
    monkeypatch.setattr(m.fcntl, "ioctl", fail)
    monkeypatch.setattr(m.select, "select", fail if failure == "select" else lambda *_: ([] if failure in {"removed", "shutdown"} else [5], [], []))
    monkeypatch.setattr(m.os, "read", (lambda *_: m.EVENT.pack(0, 0, 0, 3, 0)) if failure == "syn" else fail)
    monkeypatch.setattr(m.os, "close", lambda _fd: stop.set())
    obj._run()
    assert "disconnect" in events or failure in {"open", "grab"}


def test_thread_start_and_absent_device(monkeypatch):
    """スレッドの起動と、未接続時の待ち直しを確認する。"""
    calls = []
    monkeypatch.setattr(m.threading.Thread, "start", lambda self: calls.append("start"))
    stop = threading.Event()
    obj = m.ProfileInput(profile(), lambda *_: None, lambda: None, stop)
    monkeypatch.setattr(m, "find_devices", lambda _: [])
    monkeypatch.setattr(stop, "wait", lambda _: stop.set())
    obj._run()
    assert calls == ["start"]
