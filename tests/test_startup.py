import wave
from io import BytesIO

from argos.services.startup import build_auth_warning_tone, build_startup_chime


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


def test_build_auth_warning_tone_returns_mono_wav():
    """本人確認の警告音を16bitモノラルWAVとして生成する。"""
    wav_data = build_auth_warning_tone(16000)

    with wave.open(BytesIO(wav_data), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16000
        assert wav_file.getnframes() > 8000


def test_build_alarm_sound_is_loud_and_distinct():
    """アラーム音は起動ジングルより大きく、割れない範囲で、くり返しの回数だけ長くなる。"""
    import array

    from argos.services.startup import build_alarm_sound

    def peak(wav_data: bytes) -> tuple[int, float]:
        """最大振幅と長さ（秒）を返す。"""
        with wave.open(BytesIO(wav_data), "rb") as wav_file:
            frames = array.array("h", wav_file.readframes(wav_file.getnframes()))
            return max(abs(x) for x in frames), wav_file.getnframes() / wav_file.getframerate()

    alarm_peak, alarm_seconds = peak(build_alarm_sound(16000))
    chime_peak, _ = peak(build_startup_chime(16000))
    assert 25000 < alarm_peak <= 32767
    assert alarm_peak > chime_peak * 3
    assert 2.0 < alarm_seconds < 3.5
    assert peak(build_alarm_sound(16000, repeats=1))[1] < alarm_seconds / 2


def test_build_alarm_sound_has_silent_gaps():
    """電子音の間と組の終わりに無音があり、指定したPCM形式を保つ。"""
    from argos.services.startup import build_alarm_sound

    with wave.open(BytesIO(build_alarm_sound(16000)), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16000
        wav_file.setpos(int(16000 * 0.09))
        assert not any(wav_file.readframes(int(16000 * 0.04)))
        wav_file.setpos(int(16000 * 0.6))
        assert not any(wav_file.readframes(int(16000 * 0.2)))
