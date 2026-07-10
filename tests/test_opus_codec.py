"""Opus コーデック（ffmpeg ラッパー）のテスト。"""

import io
import shutil
import wave

import pytest

from argos.services.opus_codec import OpusCodecError, decode_opus_to_wav, encode_wav_to_opus

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が必要")


def _make_wav(sample_rate: int = 16000, frames: int = 8000) -> bytes:
    """テスト用の16bit モノラル WAV バイト列を生成する。"""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        # 220Hz 相当の単純な矩形波でサイズのある音声を作る。
        pcm = bytearray()
        for index in range(frames):
            value = 8000 if (index // 36) % 2 == 0 else -8000
            pcm += value.to_bytes(2, "little", signed=True)
        wav_file.writeframes(bytes(pcm))
    return buffer.getvalue()


def test_encode_then_decode_roundtrip():
    """WAV→Opus→WAV のラウンドトリップが成立し、WAV として読み戻せる。"""
    wav_data = _make_wav()
    opus_data = encode_wav_to_opus(wav_data, "24k")
    assert opus_data[:4] == b"OggS"
    # 圧縮が効いて元の WAV より小さいこと。
    assert len(opus_data) < len(wav_data)

    decoded = decode_opus_to_wav(opus_data)
    with wave.open(io.BytesIO(decoded), "rb") as wav_file:
        assert wav_file.getsampwidth() == 2
        assert wav_file.getnchannels() == 1
        assert wav_file.getnframes() > 0


def test_encode_invalid_input_raises():
    """壊れた入力では OpusCodecError を送出する。"""
    with pytest.raises(OpusCodecError):
        encode_wav_to_opus(b"not-a-wav-file")
