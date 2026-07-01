"""期限到達リマインダーの送信処理。"""

from __future__ import annotations

from datetime import datetime

from argos_reminder.argos_client import ArgosClient
from argos_reminder.store import ReminderStore


def run_due_once(store: ReminderStore, client: ArgosClient, now: datetime) -> int:
    """期限到達済みリマインダーを1回だけ送信し、送信件数を返す。"""
    reminders = store.load()
    location = _load_location_if_needed(reminders, client)
    sent_count = 0
    updated = []
    for reminder in reminders:
        if reminder.is_due(now, location):
            try:
                client.send_reminder(reminder)
            except Exception:
                updated.append(reminder)
                continue
            else:
                updated.append(reminder.mark_sent(now))
                sent_count += 1
        else:
            updated.append(reminder)
    if updated != reminders:
        store.save(updated)
    return sent_count


def _load_location_if_needed(reminders: list, client: ArgosClient) -> tuple[float, float] | None:
    """未送信の位置リマインダーがある場合だけ現在地を取得する。"""
    if not any(reminder.kind == "location" and reminder.sent_at is None for reminder in reminders):
        return None
    return client.get_location()
