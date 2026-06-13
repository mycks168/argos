"""VOICEVOX クライアント。"""

from __future__ import annotations

import requests


class VoicevoxClient:
    """VOICEVOX Engine でテキストから WAV を生成する。"""

    def __init__(self, base_url: str, speaker: int, sample_rate: int, speed_scale: float, volume_scale: float = 1.0) -> None:
        """API のベース URL、話者、出力サンプリングレート、話速、音量を保持する。"""
        self._base_url = base_url.rstrip("/")
        self._speaker = speaker
        self._sample_rate = sample_rate
        self._speed_scale = speed_scale
        self._volume_scale = volume_scale

    def synthesize(self, text: str, speaker: int | None = None) -> bytes:
        """VOICEVOX の audio_query と synthesis を呼び出して WAV を返す。"""
        speaker_id = self._speaker if speaker is None else speaker
        query_response = requests.post(
            f"{self._base_url}/audio_query",
            params={"text": text, "speaker": speaker_id},
            timeout=10,
        )
        if query_response.status_code != 200:
            raise RuntimeError(f"VOICEVOX audio_query エラー {query_response.status_code}: {query_response.text[:200]}")
        query = query_response.json()
        query["outputSamplingRate"] = self._sample_rate
        query["speedScale"] = self._speed_scale
        query["volumeScale"] = self._volume_scale
        synth_response = requests.post(
            f"{self._base_url}/synthesis",
            params={"speaker": speaker_id},
            json=query,
            headers={"Content-Type": "application/json"},
            timeout=60,
        )
        if synth_response.status_code != 200:
            raise RuntimeError(f"VOICEVOX synthesis エラー {synth_response.status_code}: {synth_response.text[:200]}")
        return synth_response.content
