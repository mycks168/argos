"""Bluetoothメディアボタンの接続・切断を知らせる短い音。"""

from __future__ import annotations

import io
import math
import struct
import threading
import time
import wave
from collections.abc import Callable


def build_bluetooth_chime(connected: bool, sample_rate: int = 48000) -> bytes:
    """接続は上昇、切断は下降する2音の16bit PCM WAVを作る。"""
    if sample_rate <= 0:
        raise ValueError("サンプルレートは正の値にしてください")
    notes = (523.25, 783.99) if connected else (783.99, 523.25)
    duration = 0.42
    note_length = 0.22
    frames = bytearray()
    for index in range(int(sample_rate * duration)):
        now = index / sample_rate
        tone = 0.0
        for start, frequency in ((0.0, notes[0]), (0.18, notes[1])):
            elapsed = now - start
            if 0 <= elapsed < note_length:
                # 音の立ち上がりと終わりを滑らかにしてクリック音を防ぐ。
                envelope = min(1.0, elapsed / 0.012, (note_length - elapsed) / 0.075)
                phase = 2 * math.pi * frequency * elapsed
                tone += envelope * (math.sin(phase) + 0.12 * math.sin(2 * phase))
        frames.extend(struct.pack("<h", int(max(-1.0, min(1.0, tone * 0.18)) * 32767)))
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(frames)
        return buffer.getvalue()


class BluetoothConnectionChime:
    """短時間の接続揺れと起動直後を除き、変化音を鳴らす。"""

    def __init__(
        self,
        play: Callable[[bool], None],
        shutdown: threading.Event,
        *,
        startup_grace: float = 3.0,
        debounce: float = 0.7,
        min_interval: float = 1.5,
        clock: Callable[[], float] = time.monotonic,
        timer_factory: Callable[..., threading.Timer] = threading.Timer,
    ) -> None:
        """再生関数と、通知の抑制時間を保持する。"""
        self._play = play
        self._shutdown = shutdown
        self._startup_grace = startup_grace
        self._debounce = debounce
        self._min_interval = min_interval
        self._clock = clock
        self._timer_factory = timer_factory
        self._started_at = clock()
        self._last_sound_at = float("-inf")
        self._connected = False
        self._stable_connected = False
        self._generation = 0
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def update(self, connected: bool) -> None:
        """入力監視の接続変化を受け、安定後の通知を予約する。"""
        with self._lock:
            if self._connected == connected or self._shutdown.is_set():
                return
            self._connected = connected
            self._generation += 1
            if self._timer is not None:
                self._timer.cancel()
            timer = self._timer_factory(self._debounce, self._notify, args=(self._generation, connected))
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _notify(self, generation: int, connected: bool) -> None:
        """古い予約や起動直後の変化を捨て、現在の状態だけ鳴らす。"""
        with self._lock:
            now = self._clock()
            if self._shutdown.is_set() or generation != self._generation or connected != self._connected:
                return
            if connected == self._stable_connected:
                return
            self._stable_connected = connected
            if now - self._started_at < self._startup_grace:
                return
            if now - self._last_sound_at < self._min_interval:
                return
            self._last_sound_at = now
        self._play(connected)
