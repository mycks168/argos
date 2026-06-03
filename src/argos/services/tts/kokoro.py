"""Kokoro TTS クライアント。"""

from __future__ import annotations

from io import BytesIO
from typing import Any


class KokoroClient:
    """Kokoro でテキストから WAV を生成する。"""

    def __init__(self, voice: str, speed: float, repo_id: str, sample_rate: int) -> None:
        """話者、話速、モデルID、出力サンプリングレートを保持する。"""
        self._voice = voice
        self._speed = speed
        self._repo_id = repo_id
        self._sample_rate = sample_rate
        self._pipeline: Any | None = None

    def synthesize(self, text: str) -> bytes:
        """Kokoro の日本語パイプラインで WAV を返す。"""
        pipeline = self._load_pipeline()
        audio_parts = []
        for result in pipeline(text, voice=self._voice, speed=self._speed):
            audio = result.audio
            if hasattr(audio, "detach"):
                audio = audio.detach().cpu().numpy()
            audio_parts.append(audio)
        if not audio_parts:
            raise RuntimeError("Kokoro が音声を生成しませんでした")

        import numpy as np
        import soundfile as sf

        samples = np.concatenate(audio_parts) if len(audio_parts) > 1 else audio_parts[0]
        wav_buffer = BytesIO()
        sf.write(wav_buffer, samples, self._sample_rate, format="WAV", subtype="PCM_16")
        return wav_buffer.getvalue()

    def _load_pipeline(self):
        """初回利用時だけ重い Kokoro パイプラインを読み込む。"""
        if self._pipeline is not None:
            return self._pipeline
        try:
            from kokoro import KPipeline
        except ImportError as exc:
            raise RuntimeError("Kokoro が未導入です。`uv sync --extra kokoro` を実行してください。") from exc
        try:
            self._pipeline = KPipeline(lang_code="j", repo_id=self._repo_id)
        except RuntimeError as exc:
            if "unidic/dicdir" in str(exc):
                raise RuntimeError(
                    "Kokoro の日本語辞書が未導入です。`uv run python -m unidic download` を実行してください。"
                ) from exc
            raise
        return self._pipeline
