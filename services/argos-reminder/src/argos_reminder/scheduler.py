"""リマインダーの登録と期限判定。"""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from argos_reminder.model import DEFAULT_LOCATION_RADIUS_M, Reminder

LOCAL_TZ = ZoneInfo("Asia/Tokyo")


def now_local() -> datetime:
    """現在時刻をローカルタイムゾーン付きで返す。"""
    return datetime.now(LOCAL_TZ)


def parse_datetime(value: str) -> datetime:
    """CLI指定の日時文字列をタイムゾーン付きdatetimeへ変換する。"""
    normalized = value.strip().replace(" ", "T")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            parsed = datetime.strptime(normalized, fmt)
            return parsed.replace(tzinfo=LOCAL_TZ)
        except ValueError:
            continue
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(LOCAL_TZ)


def create_reminder(
    scheduled_at: datetime,
    title: str,
    text: str = "",
    source: str = "Reminder",
    sound: bool = True,
    speak: bool = True,
    created_at: datetime | None = None,
) -> Reminder:
    """新しいリマインダーを作成する。"""
    created = created_at or now_local()
    reminder_id = f"{scheduled_at.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    return Reminder(
        id=reminder_id,
        scheduled_at=scheduled_at,
        title=title,
        text=text,
        source=source,
        sound=sound,
        speak=speak,
        created_at=created,
    )


def create_location_reminder(
    title: str,
    target_lat: float,
    target_lon: float,
    radius_m: float = DEFAULT_LOCATION_RADIUS_M,
    text: str = "",
    source: str = "Reminder",
    sound: bool = True,
    speak: bool = True,
    created_at: datetime | None = None,
) -> Reminder:
    """新しい位置リマインダーを作成する。"""
    created = created_at or now_local()
    reminder_id = f"loc-{created.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    return Reminder(
        id=reminder_id,
        scheduled_at=None,
        title=title,
        text=text,
        source=source,
        sound=sound,
        speak=speak,
        created_at=created,
        kind="location",
        target_lat=target_lat,
        target_lon=target_lon,
        radius_m=radius_m,
    )


def collect_due(reminders: list[Reminder], now: datetime) -> tuple[list[Reminder], list[Reminder]]:
    """期限到達済みと未到達を分ける。"""
    due = [reminder for reminder in reminders if reminder.is_due(now)]
    return due, reminders
