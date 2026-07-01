"""argos-reminder 常駐プロセス。"""

from __future__ import annotations

import time

from argos_reminder.argos_client import ArgosClient
from argos_reminder.config import load_settings
from argos_reminder.runner import run_due_once
from argos_reminder.scheduler import now_local
from argos_reminder.store import ReminderStore


def main() -> int:
    """期限到達リマインダーを定期的に送信する。"""
    settings = load_settings()
    store = ReminderStore(settings.state_path)
    client = ArgosClient(settings.dashboard_url, settings.dashboard_token)
    while True:
        run_due_once(store, client, now_local())
        time.sleep(max(1.0, settings.poll_seconds))
