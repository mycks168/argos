import struct

import pytest

from argos.services.dashboard.audio_devices import list_audio_devices, measure_microphone, play_speaker_test


class Result:
    """subprocess.runのテスト用結果。"""

    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_list_audio_devices_returns_friendly_hardware_names(monkeypatch):
    """ALSA一覧からマイクとスピーカーの表示名を作る。"""
    capture = "card 2: Microphone [USB Microphone], device 0: USB Audio [USB Audio]\n"
    playback = "card 0: vc4hdmi0 [vc4-hdmi-0], device 0: HDMI [HDMI]\n"

    def fake_run(command, **_kwargs):
        return Result(stdout=capture if command[0] == "arecord" else playback)

    monkeypatch.setattr("argos.services.dashboard.audio_devices.subprocess.run", fake_run)

    devices = list_audio_devices()

    assert devices["inputs"][1]["value"] == "plughw:CARD=Microphone,DEV=0"
    assert "USB Microphone" in devices["inputs"][1]["label"]
    assert devices["outputs"][1]["value"] == "plughw:CARD=vc4hdmi0,DEV=0"


def test_measure_microphone_returns_rms_level(monkeypatch):
    """短時間録音したPCMから入力レベルを返す。"""
    pcm = struct.pack("<4h", 1000, -1000, 1000, -1000)
    monkeypatch.setattr(
        "argos.services.dashboard.audio_devices.subprocess.run",
        lambda *_args, **_kwargs: Result(stdout=pcm),
    )

    result = measure_microphone("default")

    assert result["ok"] is True
    assert result["rms"] == 1000.0
    assert result["level"] > 0


def test_play_speaker_test_sends_wav_to_selected_device(monkeypatch):
    """選択したデバイスへWAV確認音を送る。"""
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs["input"]))
        return Result()

    monkeypatch.setattr("argos.services.dashboard.audio_devices.subprocess.run", fake_run)

    result = play_speaker_test("sysdefault")

    assert result["ok"] is True
    assert calls[0][0] == ["aplay", "-q", "-D", "sysdefault", "-"]
    assert calls[0][1].startswith(b"RIFF")


@pytest.mark.parametrize("device", ["", "bad\nname", "x" * 201])
def test_audio_test_rejects_invalid_device_names(device):
    """空や制御文字を含むデバイス名を拒否する。"""
    with pytest.raises(ValueError, match="正しくありません"):
        play_speaker_test(device)
