"""会話表示履歴とセッション引き継ぎ要約を保存する。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ConversationStore:
    """スロット別の会話履歴と引き継ぎ要約をJSONで管理する。"""

    def __init__(self, path: Path, enabled: bool, max_messages: int = 100) -> None:
        """保存先、機能の有効状態、スロットごとの上限を保持する。"""
        self._path = path
        self._enabled = enabled
        self._max_messages = max(1, max_messages)

    def load_histories(self) -> dict[str, list[dict[str, Any]]]:
        """保存済みの全スロット履歴を返す。"""
        if not self._enabled:
            return {}
        data = self._read()
        histories = data.get("histories", {})
        if not isinstance(histories, dict):
            return {}
        return {
            str(key): [item for item in value if isinstance(item, dict)][-self._max_messages :]
            for key, value in histories.items()
            if isinstance(value, list)
        }

    def save_histories(self, histories: dict[str, list[dict[str, Any]]]) -> None:
        """全スロット履歴を上限付きで保存する。"""
        if not self._enabled:
            return
        data = self._read()
        data["histories"] = {
            key: messages[-self._max_messages :]
            for key, messages in histories.items()
        }
        self._write(data)

    def load_memory(self, key: str) -> str:
        """指定スロットの未注入要約を返す。"""
        if not self._enabled:
            return ""
        memories = self._read().get("memories", {})
        return str(memories.get(key, "")) if isinstance(memories, dict) else ""

    def save_memory(self, key: str, summary: str) -> None:
        """指定スロットの引き継ぎ要約を保存する。"""
        if not self._enabled:
            return
        data = self._read()
        memories = data.setdefault("memories", {})
        if isinstance(memories, dict):
            memories[key] = summary
        self._write(data)

    def clear_memory(self, key: str) -> None:
        """注入を終えたスロットの要約を削除する。"""
        if not self._enabled:
            return
        data = self._read()
        memories = data.get("memories", {})
        if not isinstance(memories, dict) or key not in memories:
            return
        del memories[key]
        self._write(data)

    def _read(self) -> dict[str, Any]:
        """保存ファイルを読み、不正または未作成なら空データを返す。"""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        """一時ファイルを書いてから置換し、中途半端なJSONを残さない。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self._path)
        self._path.chmod(0o600)
