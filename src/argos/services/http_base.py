"""ARGOSの軽量HTTPサーバー共通処理。

ダッシュボードAPIとAgent Runner APIは、どちらも標準ライブラリの
``BaseHTTPRequestHandler`` 上に、JSONボディの読み取り・JSONレスポンス送信・
Bearerトークン照合という同じ処理を持っていた。ここへ集約する。
"""

from __future__ import annotations

import hmac
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any


log = logging.getLogger(__name__)


def bearer_header_matches(header: str, token: str) -> bool:
    """Authorizationヘッダーが ``Bearer <token>`` と定数時間で一致するか返す。

    タイミング攻撃でトークンを推測されないよう ``hmac.compare_digest`` を使う。
    """
    return hmac.compare_digest(header.encode("utf-8"), f"Bearer {token}".encode("utf-8"))


class JsonRequestHandler(BaseHTTPRequestHandler):
    """JSONボディの読み取りとJSONレスポンス送信を共通化する基底ハンドラ。"""

    def _read_json(self, max_bytes: int, *, allow_empty: bool = False) -> dict[str, Any]:
        """リクエストボディをJSONオブジェクトとして読む。

        Args:
            max_bytes: 受け付ける最大ボディサイズ。
            allow_empty: 本文が空のとき、``True`` なら空dictを返し、``False`` なら不正として扱う。
        """
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Content-Length が不正です") from exc
        if length < 0 or length > max_bytes:
            raise ValueError("リクエストサイズが不正です")
        if length == 0:
            if allow_empty:
                return {}
            raise ValueError("リクエストサイズが不正です")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("JSONが不正です") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSONオブジェクトを送信してください")
        return payload

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        """JSONレスポンスを返す。"""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
