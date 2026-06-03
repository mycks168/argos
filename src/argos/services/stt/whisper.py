"""faster-whisper クライアント。"""

from __future__ import annotations

import os
from typing import Any


class FasterWhisperClient:
    """faster-whisper でローカル文字起こしを行う。"""

    def __init__(self, model_size: str, language: str, device: str, compute_type: str) -> None:
        """モデルサイズ、言語、実行デバイス、計算型を保持する。"""
        self._model_size = model_size
        self._language = language
        self._device = device
        self._compute_type = compute_type
        self._model: Any | None = None

    def transcribe(self, wav_path: str) -> str:
        """WAV ファイルを faster-whisper で文字起こしする。"""
        if not os.path.exists(wav_path):
            raise FileNotFoundError(f"WAV ファイルが見つかりません: {wav_path}")
        model = self._load_model()
        segments, _info = model.transcribe(wav_path, language=self._language)
        return "".join(segment.text for segment in segments).strip()

    def _load_model(self):
        """初回利用時だけWhisperモデルを読み込む。"""
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("faster-whisper が未導入です。`uv sync --extra whisper` を実行してください。") from exc
        self._model = WhisperModel(
            self._model_size,
            device=self._device,
            compute_type=self._compute_type,
        )
        return self._model
