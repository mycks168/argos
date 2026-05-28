"""tts-filter クライアント。"""

from __future__ import annotations

import requests


class TtsFilterClient:
    """読み上げ前のテキスト正規化を行う。"""

    def __init__(self, base_url: str, bearer_token: str) -> None:
        """API のベース URL と Bearer トークンを保持する。"""
        self._base_url = base_url.rstrip("/")
        self._bearer_token = bearer_token

    def normalize(self, text: str) -> str:
        """テキストを TTS 向けに正規化する。失敗時は元テキストを返す。"""
        if not self._base_url or not self._bearer_token:
            return text
        try:
            response = requests.post(
                f"{self._base_url}/normalize",
                json={"text": text},
                headers={"Authorization": f"Bearer {self._bearer_token}"},
                timeout=10,
            )
        except requests.RequestException:
            return text
        if response.status_code != 200:
            return text
        return str(response.json().get("normalized", text))

