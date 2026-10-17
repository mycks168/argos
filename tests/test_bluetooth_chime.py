"""Bluetooth接続音と鳴動条件のテスト。"""

import struct
import threading
import wave
from io import BytesIO
from types import SimpleNamespace

import pytest

from argos.core.app import ArgosApp
from argos.services.bluetooth_chime import BluetoothConnectionChime, build_bluetooth_chime


def _crossings(samples: tuple[int, ...], start: int, end: int) -> int:
    """指定区間の正方向ゼロ交差を数えて音の高さを比較する。"""
    window = samples[start:end]
    return sum(left <= 0 < right for left, right in zip(window, window[1:]))


def test_bluetooth_chimes_are_short_and_pitch_changes_by_direction():
    """接続音は上昇し、切断音は下降する短いWAVになる。"""
    rate = 8000
    for connected in (True, False):
        with wave.open(BytesIO(build_bluetooth_chime(connected, rate)), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
            assert wav_file.getframerate() == rate
            assert wav_file.getnframes() < rate / 2
            samples = struct.unpack(f"<{wav_file.getnframes()}h", wav_file.readframes(wav_file.getnframes()))
        assert samples[0] == 0
        assert max(abs(sample) for sample in samples) < 12000
        first = _crossings(samples, int(rate * 0.04), int(rate * 0.12))
        second = _crossings(samples, int(rate * 0.23), int(rate * 0.31))
        assert (second > first) is connected


def test_bluetooth_chime_rejects_invalid_sample_rate():
    """不正なサンプルレートではWAVを作らない。"""
    with pytest.raises(ValueError):
        build_bluetooth_chime(True, 0)


class FakeTimer:
    """通知の待機と取り消しを手動で進めるタイマー。"""

    def __init__(self, delay, callback, args):
        """呼び出し先を保持する。"""
        self.delay = delay
        self.callback = callback
        self.args = args
        self.cancelled = False
        self.daemon = False

    def start(self):
        """実時間では待たず、fireまで保留する。"""

    def cancel(self):
        """古い通知を無効にする。"""
        self.cancelled = True

    def fire(self):
        """取り消されていなければ通知する。"""
        if not self.cancelled:
            self.callback(*self.args)


def test_bluetooth_chime_silences_startup_and_announces_stable_changes():
    """初回接続は無音、安定した切断と再接続だけ鳴らす。"""
    now = [0.0]
    timers = []
    sounds = []

    def make_timer(delay, callback, args):
        """テスト用タイマーを収集する。"""
        timer = FakeTimer(delay, callback, args)
        timers.append(timer)
        return timer

    chime = BluetoothConnectionChime(
        sounds.append, threading.Event(), clock=lambda: now[0], timer_factory=make_timer
    )
    chime.update(True)
    now[0] = 1.0
    timers[-1].fire()
    assert sounds == []

    now[0] = 5.0
    chime.update(False)
    chime.update(True)
    assert timers[-2].cancelled
    timers[-1].fire()
    assert sounds == []

    now[0] = 7.0
    chime.update(False)
    timers[-1].fire()
    assert sounds == [False]

    now[0] = 9.0
    chime.update(True)
    timers[-1].fire()
    assert sounds == [False, True]


def test_bluetooth_chime_throttles_repeated_events_and_stops_on_shutdown():
    """短時間の連続通知と終了後の通知を止める。"""
    now = [0.0]
    timers = []
    sounds = []
    shutdown = threading.Event()

    def make_timer(delay, callback, args):
        """テスト用タイマーを収集する。"""
        timer = FakeTimer(delay, callback, args)
        timers.append(timer)
        return timer

    chime = BluetoothConnectionChime(sounds.append, shutdown, clock=lambda: now[0], timer_factory=make_timer)
    now[0] = 4.0
    chime.update(True)
    timers[-1].fire()
    assert sounds == [True]

    now[0] = 4.5
    chime.update(False)
    timers[-1].fire()
    assert sounds == [True]

    shutdown.set()
    now[0] = 7.0
    chime.update(True)
    assert sounds == [True]


def test_argos_plays_chime_without_interrupting_muted_or_recording_state():
    """通常時だけ既存の音声出力へ渡し、ミュートと録音は優先する。"""
    sounds = []
    app = ArgosApp.__new__(ArgosApp)
    app._settings = SimpleNamespace(dry_run=False, voicevox_sample_rate=8000)
    app._speech = SimpleNamespace(is_muted=lambda: False)
    app._recorder = SimpleNamespace(is_recording=False)
    app._audio = SimpleNamespace(is_held=False, play_wav=sounds.append)

    app._play_bluetooth_chime(True)
    assert len(sounds) == 1
    assert sounds[0].startswith(b"RIFF")

    app._speech.is_muted = lambda: True
    app._play_bluetooth_chime(False)
    assert len(sounds) == 1
    app._speech.is_muted = lambda: False
    app._recorder.is_recording = True
    app._play_bluetooth_chime(False)
    assert len(sounds) == 1
    app._recorder.is_recording = False
    app._audio.is_held = True
    app._play_bluetooth_chime(False)
    assert len(sounds) == 1
