"""設定画面向けのALSA音声デバイス検出と動作確認。"""

from __future__ import annotations

import math
import re
import struct
import subprocess
import wave
from io import BytesIO
from typing import Any


ALSA_HARDWARE_LINE = re.compile(
    r"^card\s+(?P<card_no>\d+):\s+(?P<card_id>[^\s]+)\s+\[(?P<card_name>[^\]]+)\],"
    r"\s+device\s+(?P<device_no>\d+):\s+(?P<device_name>[^\[]+)"
)


def list_audio_devices() -> dict[str, list[dict[str, str]]]:
    """接続中の録音・再生デバイスを表示名付きで返す。"""
    return {
        "inputs": _list_hardware_devices(["arecord", "-l"], "マイク"),
        "outputs": _list_hardware_devices(["aplay", "-l"], "スピーカー"),
    }


def measure_microphone(device: str, duration_seconds: float = 1.0) -> dict[str, Any]:
    """指定マイクを短時間録音し、RMS入力レベルを0〜100で返す。"""
    device = _safe_device(device)
    command = [
        "arecord",
        "-q",
        "-D",
        device,
        "-d",
        str(max(1, round(duration_seconds))),
        "-f",
        "S16_LE",
        "-r",
        "16000",
        "-c",
        "1",
        "-t",
        "raw",
    ]
    result = subprocess.run(command, capture_output=True, timeout=duration_seconds + 3, check=False)
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="ignore").strip()
        raise ValueError(f"マイクを確認できません: {error[-300:] or '録音に失敗しました'}")
    if len(result.stdout) < 2:
        raise ValueError("マイクから音声を取得できませんでした")
    samples = struct.unpack(f"<{len(result.stdout) // 2}h", result.stdout[: len(result.stdout) // 2 * 2])
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    level = min(100, round(rms / 32767 * 500))
    return {"ok": True, "rms": round(rms, 1), "level": level}


def play_speaker_test(device: str) -> dict[str, Any]:
    """指定スピーカーへ短い確認音を再生する。"""
    device = _safe_device(device)
    wav_bytes = _make_test_tone()
    result = subprocess.run(
        ["aplay", "-q", "-D", device, "-"],
        input=wav_bytes,
        capture_output=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="ignore").strip()
        raise ValueError(f"テスト音を再生できません: {error[-300:] or '再生に失敗しました'}")
    return {"ok": True, "message": "テスト音を再生しました"}


def _list_hardware_devices(command: list[str], kind: str) -> list[dict[str, str]]:
    """arecord/aplayのハードウェア一覧を選択肢へ変換する。"""
    devices = [{"value": "default", "label": f"システム標準の{kind}（default）"}]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return devices
    for line in result.stdout.splitlines():
        match = ALSA_HARDWARE_LINE.match(line.strip())
        if not match:
            continue
        values = match.groupdict()
        device = f"plughw:CARD={values['card_id']},DEV={values['device_no']}"
        label = f"{values['card_name']} / {values['device_name'].strip()}（{device}）"
        devices.append({"value": device, "label": label})
    return devices


def _safe_device(device: object) -> str:
    """コマンド引数として扱える短いALSAデバイス名だけを許可する。"""
    if not isinstance(device, str) or not device.strip() or len(device) > 200:
        raise ValueError("音声デバイス名が正しくありません")
    if any(character in device for character in ("\x00", "\n", "\r")):
        raise ValueError("音声デバイス名が正しくありません")
    return device.strip()


def _make_test_tone() -> bytes:
    """聞き取りやすい短い880HzのWAV確認音を生成する。"""
    sample_rate = 16000
    frames = bytearray()
    for index in range(round(sample_rate * 0.35)):
        envelope = min(1.0, index / 500, (sample_rate * 0.35 - index) / 500)
        sample = round(7000 * envelope * math.sin(2 * math.pi * 880 * index / sample_rate))
        frames.extend(struct.pack("<h", sample))
    output = BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(frames)
    return output.getvalue()
