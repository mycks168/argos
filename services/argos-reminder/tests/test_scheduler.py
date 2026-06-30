from datetime import datetime
from zoneinfo import ZoneInfo

from argos_reminder.scheduler import create_location_reminder, create_reminder, parse_datetime


def test_parse_datetime_uses_jst_for_naive_text():
    """タイムゾーンなしの日時はJSTとして扱う。"""
    parsed = parse_datetime("2026-06-19 18:30")

    assert parsed == datetime(2026, 6, 19, 18, 30, tzinfo=ZoneInfo("Asia/Tokyo"))


def test_create_reminder_sets_defaults():
    """リマインダー作成時に通知音と読み上げを既定で有効にする。"""
    scheduled_at = parse_datetime("2026-06-19 18:30")
    created_at = parse_datetime("2026-06-19 12:00")

    reminder = create_reminder(scheduled_at, "旅費申請", created_at=created_at)

    assert reminder.id.startswith("20260619183000-")
    assert reminder.title == "旅費申請"
    assert reminder.sound is True
    assert reminder.speak is True
    assert reminder.created_at == created_at


def test_create_location_reminder_sets_target():
    """位置リマインダーは目的地と半径を保持する。"""
    created_at = parse_datetime("2026-06-19 12:00")

    reminder = create_location_reminder("到着", 35.0, 139.0, created_at=created_at)

    assert reminder.id.startswith("loc-20260619120000-")
    assert reminder.kind == "location"
    assert reminder.scheduled_at is None
    assert reminder.target_lat == 35.0
    assert reminder.target_lon == 139.0
    assert reminder.radius_m == 100.0
