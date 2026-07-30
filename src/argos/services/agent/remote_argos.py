"""別のArgosを会話スロットとして利用するクライアント。"""

from __future__ import annotations

import json
from collections.abc import Iterable
from urllib.parse import urlencode

import requests

from argos.config import AgentSlot, Settings


class RemoteArgosClient:
    """リモートArgosのTerminal APIへテキストターンを転送する。"""

    def __init__(self, settings: Settings, slot: AgentSlot) -> None:
        """表示スロットに対応する接続設定を保持する。"""
        self._slot = slot
        self._settings = settings
        if slot.slot_type != "remote" or not slot.remote_url:
            raise ValueError(f"リモートArgos設定が見つかりません: {slot.name}")

    @property
    def current_name(self) -> str:
        """ローカル側の表示スロット名を返す。"""
        return self._slot.name

    @property
    def current_provider(self) -> str:
        """仮想provider名を返す。"""
        return self._slot.provider

    @property
    def current_model(self) -> str:
        """表示用モデル名を返す。"""
        return self._slot.model or f"{self._slot.remote_provider}@remote"

    def next_slot(self) -> str:
        """単一スロットなので同じ名前を返す。"""
        return self.current_name

    def select_slot(self, name: str, provider: str) -> str:
        """単一スロットが一致する場合だけ選択する。"""
        if name != self.current_name or provider != self.current_provider:
            raise ValueError(f"エージェントスロットが見つかりません: {name} ({provider})")
        return self.current_name

    def reset_current(self) -> None:
        """リモートスロットを選択してセッションリセットを依頼する。"""
        self._select_remote_slot()
        response = requests.post(
            f"{self._slot.remote_url}/api/control",
            headers=self._headers(json_content=True),
            json={"action": "reset_agent_session"},
            timeout=10,
        )
        response.raise_for_status()

    def ask(self, prompt: str) -> str:
        """リモートArgosへ送り、最終応答を返す。"""
        return "".join(self.ask_stream(prompt))

    def ask_stream(self, prompt: str) -> Iterable[str]:
        """リモートArgosのSSEからテキスト差分を返す。"""
        self._select_remote_slot()
        response = requests.post(
            f"{self._slot.remote_url}/api/terminal/turn",
            headers=self._headers(text_content=True),
            data=prompt.encode("utf-8"),
            stream=True,
            timeout=(5, self._settings.remote_argos_timeout_seconds),
        )
        response.raise_for_status()
        # requestsはcharset未指定のtext/*をISO-8859-1とみなすため、SSEはUTF-8へ固定する。
        response.encoding = "utf-8"
        try:
            for raw_line in response.iter_lines(decode_unicode=True):
                line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8")
                if not line.startswith("data: "):
                    continue
                payload = json.loads(line[6:])
                event = str(payload.get("event", ""))
                if event == "text":
                    delta = str(payload.get("delta", ""))
                    if delta:
                        yield delta
                elif event == "error":
                    raise RuntimeError(str(payload.get("message", "リモートArgosでエラーが発生しました")))
        finally:
            response.close()

    def load_current_history(self) -> list[dict[str, object]]:
        """リモート側の対象スロットから会話履歴を取得する。"""
        query = urlencode({"name": self._slot.remote_name, "provider": self._slot.remote_provider})
        response = requests.get(
            f"{self._slot.remote_url}/api/terminal/history?{query}",
            headers=self._headers(),
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        messages = payload.get("messages", [])
        return [item for item in messages if isinstance(item, dict)] if isinstance(messages, list) else []

    def _select_remote_slot(self) -> None:
        """リモートArgosで対象スロットを選択する。"""
        response = requests.post(
            f"{self._slot.remote_url}/api/terminal/slots/select",
            headers=self._headers(json_content=True),
            json={
                "name": self._slot.remote_name,
                "provider": self._slot.remote_provider,
            },
            timeout=10,
        )
        response.raise_for_status()

    def _headers(self, *, json_content: bool = False, text_content: bool = False) -> dict[str, str]:
        """認証とコンテンツ種別を含むHTTPヘッダーを返す。"""
        headers = {"Accept": "text/event-stream"}
        if self._slot.remote_token:
            headers["Authorization"] = f"Bearer {self._slot.remote_token}"
        if json_content:
            headers["Content-Type"] = "application/json"
        if text_content:
            headers["Content-Type"] = "text/plain; charset=utf-8"
        return headers
