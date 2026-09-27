"""長い通知の本文を、Ollamaで短い1文に要約する。

通知の中身を外部のサービスへ送らないよう、LAN内のOllamaを使う。失敗・時間切れのときは
Noneを返し、呼び出し側は先頭だけを読む。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

import requests

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "あなたは、運転中の人へスマホの通知を読み上げるための要約係です。"
    "通知の本文を、日本語の話し言葉で、{max_chars}文字以内の1文に要約してください。"
    "URL、記号、IDや数字の羅列は省き、要点だけを書いてください。要約文だけを出力してください。"
)


class OllamaSummarizer:
    """OllamaのチャットAPIで要約する。HTTPの送信は外から渡せるので、実機なしで検証できる。"""

    def __init__(
        self,
        *,
        url: str,
        model: str,
        max_chars: int = 40,
        timeout: float = 15.0,
        keep_alive: str = "30m",
        post: Callable[..., Any] = requests.post,
    ) -> None:
        """接続先、モデル、要約の長さ、待ち時間を保持する。"""
        self._url = url.rstrip("/")
        self._model = model
        self._max_chars = max_chars
        self._timeout = timeout
        self._keep_alive = keep_alive
        self._post = post

    def summarize(self, text: str) -> str | None:
        """本文を1文に要約して返す。失敗したらNoneを返す。"""
        payload = {
            "model": self._model,
            "stream": False,
            "think": False,
            "keep_alive": self._keep_alive,
            "options": {"temperature": 0.2, "num_predict": self._max_chars * 3},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT.format(max_chars=self._max_chars)},
                {"role": "user", "content": text},
            ],
        }
        try:
            response = self._post(f"{self._url}/api/chat", json=payload, timeout=self._timeout)
            response.raise_for_status()
            content = str(response.json().get("message", {}).get("content", ""))
        except (requests.RequestException, ValueError, AttributeError) as exc:
            log.warning("通知の要約に失敗しました: %s", exc)
            return None
        return _clean(content) or None


def _clean(content: str) -> str:
    """モデルの出力から、最初の1行を取り出し、囲みの記号を除く。"""
    lines = [line.strip() for line in content.strip().splitlines() if line.strip()]
    if not lines:
        return ""
    return re.sub(r"^[「『\"'*]+|[」』\"'*]+$", "", lines[0]).strip()
