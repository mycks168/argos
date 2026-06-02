import wave
from io import BytesIO

from argos.services.startup import build_startup_chime


def test_build_startup_chime_returns_mono_wav():
    """起動音を16bitモノラルWAVとして生成する。"""
    wav_data = build_startup_chime(16000)

    with wave.open(BytesIO(wav_data), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16000
        assert wav_file.getnframes() > 16000


def test_build_startup_chime_contains_audio_samples():
    """無音ではないWAVを生成する。"""
    wav_data = build_startup_chime(8000)

    assert any(byte not in (0, 128) for byte in wav_data[44:])
