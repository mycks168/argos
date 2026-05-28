"""VOICEVOX クライアント。"""

from __future__ import annotations

import requests


class VoicevoxClient:
    """VOICEVOX Engine でテキストから WAV を生成する。"""

    def __init__(self, base_url: str, speaker: int, sample_rate: int) -> None:
        """API のベース URL、話者、出力サンプリングレートを保持する。"""
        self._base_url = base_url.rstrip("/")
        self._speaker = speaker
        self._sample_rate = sample_rate

    def synthesize(self, text: str) -> bytes:
        """VOICEVOX の audio_query と synthesis を呼び出して WAV を返す。"""
        query_response = requests.post(
            f"{self._base_url}/audio_query",
            params={"text": text, "speaker": self._speaker},
            timeout=10,
        )
        if query_response.status_code != 200:
            raise RuntimeError(f"VOICEVOX audio_query エラー {query_response.status_code}: {query_response.text[:200]}")
        query = query_response.json()
        query["outputSamplingRate"] = self._sample_rate
        synth_response = requests.post(
            f"{self._base_url}/synthesis",
            params={"speaker": self._speaker},
            json=query,
            headers={"Content-Type": "application/json"},
            timeout=60,
        )
        if synth_response.status_code != 200:
            raise RuntimeError(f"VOICEVOX synthesis エラー {synth_response.status_code}: {synth_response.text[:200]}")
        return synth_response.content

