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


def build_alarm_sound(sample_rate: int = 48000, repeats: int = 3) -> bytes:
    """タイマーの終わりなどを知らせる、目立つアラーム音（ピピピピッ×repeats）を生成する。

    起動ジングルと聞き分けられるよう、高い音の短い電子音を4回ずつ鳴らし、少し休んでくり返す。
    倍音を足して小さなスピーカーでも通るようにし、振幅は割れない範囲で大きくする
    （最終的な音量は ARGOS の音量設定に従う）。
    """
    beep_on, beep_off, rest = 0.085, 0.055, 0.32
    group = 4 * (beep_on + beep_off) + rest
    duration_seconds = repeats * group
    frame_count = int(sample_rate * duration_seconds)
    frames = bytearray()
    for index in range(frame_count):
        t = index / sample_rate
        within = t % group
        beep = int(within / (beep_on + beep_off))
        offset = within - beep * (beep_on + beep_off)
        sample = 0
        if beep < 4 and offset < beep_on:
            # 立ち上がりと終わりを数ミリ秒だけ丸め、プチッという音を防ぐ。
            edge = min(offset / 0.004, (beep_on - offset) / 0.004, 1.0)
            frequency = 2093.0 if beep % 2 == 0 else 2637.0  # C7 と E7 を交互に
            tone = (math.sin(2 * math.pi * frequency * t)
                    + 0.45 * math.sin(2 * math.pi * frequency * 2 * t)
                    + 0.25 * math.sin(2 * math.pi * frequency * 3 * t))
            sample = int(22500 * edge * tone)
        frames.extend(struct.pack("<h", max(-32768, min(32767, sample))))
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(bytes(frames))
        return buffer.getvalue()


def build_auth_warning_tone(sample_rate: int = 48000) -> bytes:
    """本人確認待ちを知らせる短い警告音を生成する。"""
    duration_seconds = 1.2
    frame_count = int(sample_rate * duration_seconds)
    frames = bytearray()
    for index in range(frame_count):
        t = index / sample_rate
        pulse = 1.0 if int(t / 0.18) % 2 == 0 else 0.35
        fade = min(t / 0.04, 1.0) * min((duration_seconds - t) / 0.12, 1.0)
        tone = math.sin(2 * math.pi * 740.0 * t) + 0.35 * math.sin(2 * math.pi * 988.0 * t)
        sample = int(7000 * fade * pulse * tone)
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
