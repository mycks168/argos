"""LLMエージェント各providerで共通のセッションID永続化。

Codex、Claude、Antigravity、Hermes の各CLIクライアントは、スロットごとに
providerのセッション/会話IDをJSONへ保存する処理をそれぞれ実装していた。
実装はほぼ同一だったため、この共通ストアへ集約する。
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from argos.config import AgentSlot


log = logging.getLogger(__name__)


def slot_key(slot: AgentSlot) -> str:
    """保存用にスロット設定から安定したキーを作る。"""
    raw = "\0".join((slot.name, slot.provider, slot.cwd))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SlotSessionStore:
    """スロット識別キーごとのセッションIDをJSONファイルへ保存する。"""

    def __init__(self, path: Path, label: str = "Agent セッションID", fallback_paths: tuple[Path, ...] = ()) -> None:
        """保存先ファイルとログ表示用ラベルを初期化する。

        Args:
            path: セッションIDを保存するJSONファイルのパス。
            label: ログに出す名称（例: "Codex セッションID"、"Antigravity 会話ID"）。
            fallback_paths: 保存ファイルが空のときに読む旧保存先の候補。
        """
        self._path = path
        self._label = label
        self._fallback_paths = fallback_paths

    def load(self, key: str) -> str:
        """指定スロットの保存済みセッションIDを返す。"""
        value = self._read().get(key, "")
        return value if isinstance(value, str) else ""

    def save(self, key: str, session_id: str) -> None:
        """指定スロットのセッションIDを保存する。"""
        if not session_id:
            return
        data = self._read()
        if data.get(key) == session_id:
            return
        data[key] = session_id
        self._write(data, action="保存")

    def clear(self, key: str) -> None:
        """指定スロットの保存済みセッションIDを削除する。"""
        data = self._read()
        if key not in data:
            return
        del data[key]
        self._write(data, action="削除")

    def _write(self, data: dict[str, str], *, action: str) -> None:
        """保存ファイルへJSONを書き込む。失敗してもログのみで継続する。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            log.exception("%sの%sに失敗しました: %s", self._label, action, self._path)

    def _read(self) -> dict[str, str]:
        """保存ファイル（なければ旧保存先候補）をJSONとして読み込む。"""
        for path in (self._path, *self._fallback_paths):
            data = self._read_path(path)
            if data:
                return data
        return {}

    def _read_path(self, path: Path) -> dict[str, str]:
        """指定ファイルをJSONとして読み込む。"""
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError:
            log.exception("%sの読み込みに失敗しました: %s", self._label, path)
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("%s保存ファイルが壊れています: %s", self._label, path)
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): value for key, value in data.items() if isinstance(value, str)}
