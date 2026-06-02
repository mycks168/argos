"""ARGOS 起動演出用の音を生成する。"""

from __future__ import annotations

import io
import math
import struct
import wave


def build_startup_chime(sample_rate: int = 48000) -> bytes:
    """短い起動ジングルを16bit PCM WAVとして生成する。"""
    duration_seconds = 2.9
    frame_count = int(sample_rate * duration_seconds)
    frames = bytearray()
    for index in range(frame_count):
        t = index / sample_rate
        global_fade = min(t / 0.18, 1.0) * min((duration_seconds - t) / 0.55, 1.0)
        tone = 0.0
        for start, frequency, amount in (
            (0.18, 261.63, 0.30),
            (0.56, 329.63, 0.24),
            (0.94, 392.00, 0.25),
            (1.36, 523.25, 0.20),
        ):
            envelope = _voice_envelope(t, start, 1.25, 0.08, 0.88)
            tone += amount * math.sin(2 * math.pi * frequency * t) * envelope
            tone += amount * 0.18 * math.sin(2 * math.pi * frequency * 2.0 * t) * envelope
        pad_envelope = _voice_envelope(t, 0.0, 2.7, 0.42, 0.9)
        tone += 0.13 * math.sin(2 * math.pi * 130.81 * t) * pad_envelope
        tone += 0.1 * math.sin(2 * math.pi * 196.0 * t) * pad_envelope
        sample = int(9000 * global_fade * tone)
        frames.extend(struct.pack("<h", max(-32768, min(32767, sample))))
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(bytes(frames))
        return buffer.getvalue()


def _voice_envelope(t: float, start: float, duration: float, attack: float, release: float) -> float:
    """指定区間の音量包絡を返す。"""
    local_time = t - start
    if local_time < 0 or local_time > duration:
        return 0.0
    return max(0.0, min(local_time / attack, (duration - local_time) / release, 1.0))
