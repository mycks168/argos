"""利用間隔に応じた挨拶を管理する。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path


log = logging.getLogger(__name__)


class GreetingManager:
    """前回利用時刻を保存し、発話時の挨拶を選ぶ。"""

    def __init__(self, state_path: str) -> None:
        """状態ファイルのパスを保持する。"""
        self._state_path = Path(state_path).expanduser()

    def greeting_on_interaction(self, now: datetime | None = None) -> str:
        """前回利用時刻から発話時の挨拶を選び、今回の時刻を保存する。"""
        current = now or datetime.now().astimezone()
        previous = self._load_last_active_at()
        self.mark_active(current)
        if previous is None or previous.date() != current.date():
            return _greeting_for_hour(current.hour)
        elapsed = current - previous
        if elapsed < timedelta(minutes=10):
            return ""
        if elapsed < timedelta(hours=3):
            return "おかえり。"
        return "久しぶり。お疲れさま。"

    def mark_active(self, now: datetime | None = None) -> None:
        """最終利用時刻を状態ファイルへ保存する。"""
        current = now or datetime.now().astimezone()
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self._state_path.with_suffix(f"{self._state_path.suffix}.tmp")
            temporary_path.write_text(
                json.dumps({"last_active_at": current.isoformat(timespec="seconds")}, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary_path.replace(self._state_path)
        except OSError:
            log.exception("挨拶状態を保存できませんでした")

    def _load_last_active_at(self) -> datetime | None:
        """保存済みの最終利用時刻を読み込む。"""
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            return datetime.fromisoformat(str(payload["last_active_at"]))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None


def _greeting_for_hour(hour: int) -> str:
    """時間帯に合う挨拶を返す。"""
    if 5 <= hour < 11:
        return "おはよう。"
    if 11 <= hour < 18:
        return "こんにちは。"
    return "こんばんは。"
