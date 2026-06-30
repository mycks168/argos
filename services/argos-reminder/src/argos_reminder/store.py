"""リマインダーのJSON保存。"""

from __future__ import annotations

import json
from pathlib import Path

from argos_reminder.model import Reminder


class ReminderStore:
    """リマインダーをJSONファイルへ保存する。"""

    def __init__(self, path: str) -> None:
        """保存先パスを設定する。"""
        self._path = Path(path).expanduser()

    def load(self) -> list[Reminder]:
        """保存済みリマインダーを読み込む。"""
        if not self._path.exists():
            return []
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        return [Reminder.from_dict(item) for item in payload.get("reminders", [])]

    def save(self, reminders: list[Reminder]) -> None:
        """リマインダー一覧を保存する。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"reminders": [reminder.to_dict() for reminder in reminders]}
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def add(self, reminder: Reminder) -> None:
        """リマインダーを追加保存する。"""
        reminders = self.load()
        reminders.append(reminder)
        self.save(reminders)

    def remove(self, reminder_id: str) -> bool:
        """指定IDのリマインダーを削除し、削除できたか返す。"""
        reminders = self.load()
        kept = [reminder for reminder in reminders if reminder.id != reminder_id]
        if len(kept) == len(reminders):
            return False
        self.save(kept)
        return True
