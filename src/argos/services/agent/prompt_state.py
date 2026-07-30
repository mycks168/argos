"""エージェントのシステム指示注入状態を保存する。"""

from __future__ import annotations

import json
import threading
from pathlib import Path


class SystemPromptStateStore:
    """システムプロンプト注入済み状態をスレッドセーフに保存する。"""

    def __init__(self, path: Path) -> None:
        """保存先ファイルと排他ロックを保持する。"""
        self._path = path
        self._lock = threading.RLock()

    def is_injected(self, key: str) -> bool:
        """指定スロットへシステムプロンプトを注入済みならTrueを返す。"""
        with self._lock:
            return bool(self._read().get(key))

    def mark_injected(self, key: str) -> None:
        """指定スロットを注入済みにする。"""
        with self._lock:
            data = self._read()
            if data.get(key) is True:
                return
            data[key] = True
            self._write(data)

    def clear(self, key: str) -> None:
        """指定スロットの注入済み状態を消す。"""
        with self._lock:
            data = self._read()
            if key not in data:
                return
            del data[key]
            self._write(data)

    def _read(self) -> dict[str, bool]:
        """保存ファイルをJSONとして読み込む。"""
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): bool(value) for key, value in data.items()}

    def _write(self, data: dict[str, bool]) -> None:
        """保存ファイルへJSONを書き込む。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            return
