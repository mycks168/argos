from argos_reminder.scheduler import create_reminder, parse_datetime
from argos_reminder.store import ReminderStore


def test_store_add_load_and_remove(tmp_path):
    """JSONファイルへリマインダーを保存、読込、削除できる。"""
    store = ReminderStore(str(tmp_path / "reminders.json"))
    reminder = create_reminder(parse_datetime("2026-06-19 18:30"), "旅費申請")

    store.add(reminder)
    loaded = store.load()

    assert loaded == [reminder]
    assert store.remove(reminder.id) is True
    assert store.load() == []
    assert store.remove(reminder.id) is False


def test_store_loads_legacy_time_reminder(tmp_path):
    """kindがない旧形式の日時リマインダーも読み込める。"""
    path = tmp_path / "reminders.json"
    path.write_text(
        """
{
  "reminders": [
    {
      "id": "legacy",
      "scheduled_at": "2026-06-19T18:30:00+09:00",
      "title": "旅費申請",
      "text": "",
      "source": "Reminder",
      "sound": true,
      "speak": true,
      "created_at": "2026-06-19T12:00:00+09:00",
      "sent_at": null
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )
    store = ReminderStore(str(path))

    loaded = store.load()

    assert loaded[0].kind == "time"
    assert loaded[0].scheduled_at == parse_datetime("2026-06-19 18:30")
