"""stt-gateway クライアント。"""

from __future__ import annotations

import os
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class SttGatewayClient:
    """WAV を stt-gateway に送信して文字起こしする。"""

    def __init__(self, base_url: str, language: str, bearer_token: str = "") -> None:
        """API のベース URL、言語、Bearerトークンを保持する。"""
        self._base_url = base_url.rstrip("/")
        self._language = language
        self._bearer_token = bearer_token
        self._session = requests.Session()
        retry = Retry(total=3, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504], allowed_methods=["POST"])
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    def transcribe(self, wav_path: str) -> str:
        """WAV ファイルを送信し、認識テキストを返す。"""
        if not os.path.exists(wav_path):
            raise FileNotFoundError(f"WAV ファイルが見つかりません: {wav_path}")
        upload_name = Path(wav_path).name
        with open(wav_path, "rb") as wav_file:
            response = self._session.post(
                f"{self._base_url}/transcribe",
                files={"file": (upload_name, wav_file, "audio/wav")},
                data={"language": self._language},
                headers=self._headers(),
                timeout=30,
            )
        if response.status_code != 200:
            raise RuntimeError(f"stt-gateway エラー {response.status_code}: {response.text[:300]}")
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"stt-gateway が失敗を返しました: {payload}")
        return str(payload.get("text", "")).strip()

    def _headers(self) -> dict[str, str]:
        """Bearerトークンがある場合だけ認証ヘッダを返す。"""
        if not self._bearer_token:
            return {}
        return {"Authorization": f"Bearer {self._bearer_token}"}
