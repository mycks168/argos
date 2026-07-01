from argos_reminder.runner import run_due_once
from argos_reminder.scheduler import create_location_reminder, create_reminder, parse_datetime
from argos_reminder.store import ReminderStore


class FakeClient:
    """送信内容を記録するテスト用クライアント。"""

    def __init__(self):
        """送信履歴を初期化する。"""
        self.sent = []
        self.location = None
        self.fail = False

    def send_reminder(self, reminder):
        """送信対象を記録する。"""
        if self.fail:
            raise OSError("send failed")
        self.sent.append(reminder)
        return {"id": "notice"}

    def get_location(self):
        """現在地を返す。"""
        return self.location


def test_run_due_once_sends_due_reminders_and_marks_sent(tmp_path):
    """期限到達済みだけを送信し、送信済みにする。"""
    store = ReminderStore(str(tmp_path / "reminders.json"))
    due = create_reminder(parse_datetime("2026-06-19 12:00"), "旅費申請")
    future = create_reminder(parse_datetime("2026-06-19 20:00"), "夕方確認")
    store.save([due, future])
    client = FakeClient()

    count = run_due_once(store, client, parse_datetime("2026-06-19 18:30"))

    loaded = store.load()
    assert count == 1
    assert [reminder.title for reminder in client.sent] == ["旅費申請"]
    assert loaded[0].sent_at == parse_datetime("2026-06-19 18:30")
    assert loaded[1].sent_at is None


def test_run_due_once_sends_location_reminder_when_inside_radius(tmp_path):
    """現在地が半径内なら位置リマインダーを送信する。"""
    store = ReminderStore(str(tmp_path / "reminders.json"))
    location = create_location_reminder("到着", 35.0, 139.0, radius_m=100)
    store.save([location])
    client = FakeClient()
    client.location = (35.0005, 139.0)

    count = run_due_once(store, client, parse_datetime("2026-06-19 18:30"))

    loaded = store.load()
    assert count == 1
    assert [reminder.title for reminder in client.sent] == ["到着"]
    assert loaded[0].sent_at == parse_datetime("2026-06-19 18:30")


def test_run_due_once_ignores_location_reminder_without_gps(tmp_path):
    """GPSが取れない場合は位置リマインダーを送信しない。"""
    store = ReminderStore(str(tmp_path / "reminders.json"))
    location = create_location_reminder("到着", 35.0, 139.0, radius_m=100)
    store.save([location])
    client = FakeClient()

    count = run_due_once(store, client, parse_datetime("2026-06-19 18:30"))

    loaded = store.load()
    assert count == 0
    assert client.sent == []
    assert loaded[0].sent_at is None


def test_run_due_once_keeps_reminder_unsent_when_delivery_fails(tmp_path):
    """送信に失敗した場合は送信済みにせず、次回ポーリングで再試行できる。"""
    store = ReminderStore(str(tmp_path / "reminders.json"))
    due = create_reminder(parse_datetime("2026-06-19 12:00"), "旅費申請")
    store.save([due])
    client = FakeClient()
    client.fail = True

    count = run_due_once(store, client, parse_datetime("2026-06-19 18:30"))

    loaded = store.load()
    assert count == 0
    assert loaded[0].sent_at is None
