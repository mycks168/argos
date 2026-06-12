import json
import sys
import types
import wave
from pathlib import Path

from argos.hardware.audio import check_audio_level
from argos.services.stt.gateway import SttGatewayClient
from argos.services.stt.whisper import FasterWhisperClient
from argos.services.tts.filter import TtsFilterClient
from argos.services.tts.kokoro import KokoroClient
from argos.services.tts.voicevox import VoicevoxClient


class Response:
    def __init__(self, status_code=200, payload=None, content=b"wav", text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = content
        self.text = text or json.dumps(self._payload)

    def json(self):
        return self._payload


def test_check_audio_level(tmp_path):
    wav_path = tmp_path / "sample.wav"
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes((1000).to_bytes(2, "little", signed=True) * 10)

    assert check_audio_level(str(wav_path)) == 1000


def test_stt_gateway_transcribe(monkeypatch, tmp_path):
    wav_path = tmp_path / "utterance-1234abcd.wav"
    wav_path.write_bytes(b"RIFFdata")
    client = SttGatewayClient("http://stt", "ja", "token")

    def fake_post(url, files, data, headers, timeout):
        assert url == "http://stt/transcribe"
        filename, file_obj, content_type = files["file"]
        assert filename == "utterance-1234abcd.wav"
        assert file_obj.read() == b"RIFFdata"
        assert content_type == "audio/wav"
        assert data == {"language": "ja"}
        assert headers == {"Authorization": "Bearer token"}
        return Response(payload={"ok": True, "text": "こんにちは"})

    monkeypatch.setattr(client._session, "post", fake_post)

    assert client.transcribe(str(wav_path)) == "こんにちは"


def test_faster_whisper_transcribe(monkeypatch, tmp_path):
    """faster-whisperでWAVを文字起こしする。"""
    wav_path = tmp_path / "sample.wav"
    wav_path.write_bytes(b"RIFFdata")
    calls = []

    class Segment:
        def __init__(self, text):
            self.text = text

    class FakeWhisperModel:
        def __init__(self, model_size, device, compute_type):
            calls.append(("init", model_size, device, compute_type))

        def transcribe(self, wav_path, language):
            calls.append(("transcribe", wav_path, language))
            return [Segment("こんにちは"), Segment("世界")], object()

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeWhisperModel))
    client = FasterWhisperClient("small", "ja", "auto", "int8")

    assert client.transcribe(str(wav_path)) == "こんにちは世界"
    assert calls[0] == ("init", "small", "auto", "int8")
    assert calls[1] == ("transcribe", str(wav_path), "ja")


def test_tts_filter_normalize(monkeypatch):
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append((url, json, headers))
        return Response(payload={"normalized": "リードミー"})

    monkeypatch.setattr("argos.services.tts.filter.requests.post", fake_post)
    client = TtsFilterClient("http://filter", "token")

    assert client.normalize("README") == "リードミー"
    assert calls[0][2]["Authorization"] == "Bearer token"


def test_voicevox_synthesize(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/audio_query"):
            return Response(payload={"speedScale": 1.0})
        return Response(content=b"wave-bytes")

    monkeypatch.setattr("argos.services.tts.voicevox.requests.post", fake_post)
    client = VoicevoxClient("http://voicevox", 2, 48000, 1.1)

    assert client.synthesize("こんにちは") == b"wave-bytes"
    assert calls[0][0] == "http://voicevox/audio_query"
    assert calls[0][1]["params"]["speaker"] == 2
    assert calls[1][1]["params"]["speaker"] == 2
    assert calls[1][1]["json"]["outputSamplingRate"] == 48000
    assert calls[1][1]["json"]["speedScale"] == 1.1


def test_voicevox_synthesize_accepts_speaker_override(monkeypatch):
    """合成ごとにVOICEVOX話者IDを上書きできる。"""
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/audio_query"):
            return Response(payload={"speedScale": 1.0})
        return Response(content=b"wave-bytes")

    monkeypatch.setattr("argos.services.tts.voicevox.requests.post", fake_post)
    client = VoicevoxClient("http://voicevox", 2, 48000, 1.1)

    assert client.synthesize("こんにちは", speaker=8) == b"wave-bytes"
    assert calls[0][1]["params"]["speaker"] == 8
    assert calls[1][1]["params"]["speaker"] == 8


def test_kokoro_synthesize_uses_japanese_pipeline(monkeypatch):
    """Kokoroの日本語パイプラインからWAVを生成する。"""
    calls = []

    class Result:
        audio = [0.0, 0.1]

    class FakePipeline:
        def __init__(self, lang_code, repo_id):
            calls.append(("init", lang_code, repo_id))

        def __call__(self, text, voice, speed):
            calls.append(("call", text, voice, speed))
            return [Result()]

    fake_numpy = types.SimpleNamespace(concatenate=lambda parts: parts[0])

    def fake_write(buffer, samples, sample_rate, format, subtype):
        calls.append(("write", samples, sample_rate, format, subtype))
        buffer.write(b"wav")

    fake_soundfile = types.SimpleNamespace(write=fake_write)
    monkeypatch.setitem(sys.modules, "kokoro", types.SimpleNamespace(KPipeline=FakePipeline))
    monkeypatch.setitem(sys.modules, "numpy", fake_numpy)
    monkeypatch.setitem(sys.modules, "soundfile", fake_soundfile)

    client = KokoroClient("jf_alpha", 1.2, "repo", 24000)

    assert client.synthesize("こんにちは") == b"wav"
    assert calls[0] == ("init", "j", "repo")
    assert calls[1] == ("call", "こんにちは", "jf_alpha", 1.2)
    assert calls[2] == ("write", [0.0, 0.1], 24000, "WAV", "PCM_16")
