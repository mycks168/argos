"""リモートARGOSの同じ端末搬送APIへ認証付きで接続する。"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import asdict

import requests

from argos.config import AgentSlot
from argos.services.agent.console_protocol import ConsoleRead
from argos.services.agent.tmux_transport import TerminalSize
from argos.services.agent.terminal_pty import IDLE_TIMEOUT_SECONDS


def remote_execution_mode(slot: AgentSlot) -> str:
    """旧版の接続先は従来スロットのままとし、会話互換性を維持する。"""
    try:
        response = requests.get(
            f"{slot.remote_url}/api/terminal/slots", headers=_headers(slot), timeout=(2, 3),
        )
        response.raise_for_status()
        slots = response.json().get("slots", [])
        return next((
            str(item.get("execution_mode", "cli")) for item in slots
            if item.get("name") == slot.remote_name and item.get("provider") == slot.remote_provider
        ), "cli")
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        return "cli"


def _headers(slot: AgentSlot) -> dict[str, str]:
    """接続先のトークンをブラウザへ渡さない。"""
    return {"Authorization": f"Bearer {slot.remote_token}"}


class RemoteConsole:
    """接続先ARGOSが所有するPTYを既存のHTTP経路で中継する。"""

    def __init__(self, slot: AgentSlot, size: TerminalSize) -> None:
        """接続先の定義済みスロット名へ変換して開く。"""
        self._slot = slot
        self._is_closed = False
        self._last_seen = time.monotonic()
        opened = self._post({
            "action": "open", "name": slot.remote_name, "provider": slot.remote_provider,
            **asdict(size),
        })
        connection_id = opened.get("connection_id")
        if not isinstance(connection_id, str):
            raise RuntimeError("リモートARGOSの端末応答が不正です")
        self._connection_id = connection_id

    @property
    def is_closed(self) -> bool:
        """終了した接続を回収できるようにする。"""
        return self._is_closed or time.monotonic() - self._last_seen > IDLE_TIMEOUT_SECONDS

    def read(self, offset: int) -> ConsoleRead:
        """リモートの長時間ポーリングも接続・読込期限を持たせる。"""
        self._last_seen = time.monotonic()
        response = requests.get(
            f"{self._slot.remote_url}/api/terminal/console", headers=_headers(self._slot),
            params={"connection_id": self._connection_id, "offset": str(offset)}, timeout=(3, 20),
        )
        if response.status_code in {400, 404, 409}:
            self._is_closed = True
            raise RuntimeError("リモート端末の接続が失効しました。再接続してください")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload.get("data"), str) or type(payload.get("offset")) is not int or type(payload.get("is_closed")) is not bool:
            raise RuntimeError("リモートARGOSの端末出力が不正です")
        self._is_closed = payload["is_closed"]
        return ConsoleRead(payload["offset"], payload["data"], self._is_closed, payload.get("has_pending_turn") is True)

    def write(self, data: bytes) -> None:
        """端末入力はコードとして解釈せず中継する。"""
        self._post({"action": "input", "connection_id": self._connection_id, "data": base64.b64encode(data).decode("ascii")})

    def resize(self, size: TerminalSize) -> None:
        """接続先の実端末クライアントの寸法を変更する。"""
        self._post({"action": "resize", "connection_id": self._connection_id, **asdict(size)})

    def close(self) -> None:
        """通信不能でも接続先のリース期限でPTYを回収する。"""
        self._is_closed = True
        try:
            self._post({"action": "close", "connection_id": self._connection_id})
        except requests.RequestException:
            return

    def _post(self, payload: dict[str, object]) -> dict[str, object]:
        """任意URLやコマンドを受け取らず、固定のAPIだけを呼ぶ。"""
        response = requests.post(
            f"{self._slot.remote_url}/api/terminal/console",
            headers={**_headers(self._slot), "Content-Type": "application/json"},
            data=json.dumps(payload).encode("utf-8"), timeout=(3, 20),
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("リモートARGOSの端末応答が不正です")
        return result
