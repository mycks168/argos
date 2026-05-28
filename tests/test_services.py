import json
import wave
from pathlib import Path

from argos.hardware.audio import check_audio_level
from argos.services.stt.gateway import SttGatewayClient
from argos.services.tts.filter import TtsFilterClient
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
    wav_path = tmp_path / "sample.wav"
    wav_path.write_bytes(b"RIFFdata")
    client = SttGatewayClient("http://stt", "ja")

    def fake_post(url, files, data, timeout):
        assert url == "http://stt/transcribe"
        assert data == {"language": "ja"}
        return Response(payload={"ok": True, "text": "こんにちは"})

    monkeypatch.setattr(client._session, "post", fake_post)

    assert client.transcribe(str(wav_path)) == "こんにちは"


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
    client = VoicevoxClient("http://voicevox", 2, 48000)

    assert client.synthesize("こんにちは") == b"wave-bytes"
    assert calls[0][0] == "http://voicevox/audio_query"
    assert calls[1][1]["json"]["outputSamplingRate"] == 48000

