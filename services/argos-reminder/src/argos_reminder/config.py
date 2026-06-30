"""argos-reminder の設定読み込み。"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """リマインダーサービスの設定値。"""

    state_path: str
    dashboard_url: str
    dashboard_token: str
    poll_seconds: float


def load_settings() -> Settings:
    """環境変数から設定を読み込む。"""
    return Settings(
        state_path=os.environ.get("ARGOS_REMINDER_STATE_PATH", "~/.local/state/argos-reminder/reminders.json"),
        dashboard_url=os.environ.get("ARGOS_DASHBOARD_URL", "http://127.0.0.1:8765"),
        dashboard_token=os.environ.get("ARGOS_DASHBOARD_TOKEN", ""),
        poll_seconds=float(os.environ.get("ARGOS_REMINDER_POLL_SECONDS", "10")),
    )
