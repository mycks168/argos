from datetime import datetime, timezone

from argos.services.greeting import GreetingManager, _greeting_for_hour


def test_first_interaction_uses_time_based_greeting(tmp_path):
    """初回発話では時間帯に合う挨拶を返す。"""
    manager = GreetingManager(str(tmp_path / "greeting.json"))

    assert manager.greeting_on_interaction(datetime(2026, 6, 2, 8, tzinfo=timezone.utc)) == "おはよう。"


def test_short_interval_does_not_greet(tmp_path):
    """短時間で再利用した場合は挨拶を省略する。"""
    manager = GreetingManager(str(tmp_path / "greeting.json"))
    manager.mark_active(datetime(2026, 6, 2, 8, tzinfo=timezone.utc))

    assert manager.greeting_on_interaction(datetime(2026, 6, 2, 8, 5, tzinfo=timezone.utc)) == ""


def test_return_after_break_uses_welcome_back(tmp_path):
    """短い休憩後は簡潔に迎える。"""
    manager = GreetingManager(str(tmp_path / "greeting.json"))
    manager.mark_active(datetime(2026, 6, 2, 8, tzinfo=timezone.utc))

    assert manager.greeting_on_interaction(datetime(2026, 6, 2, 9, tzinfo=timezone.utc)) == "おかえり。"


def test_return_after_long_break_uses_otsukaresama(tmp_path):
    """長時間経過後は労う。"""
    manager = GreetingManager(str(tmp_path / "greeting.json"))
    manager.mark_active(datetime(2026, 6, 2, 8, tzinfo=timezone.utc))

    assert manager.greeting_on_interaction(datetime(2026, 6, 2, 14, tzinfo=timezone.utc)) == "久しぶり。お疲れさま。"


def test_new_day_uses_time_based_greeting(tmp_path):
    """日付が変わった場合は時間帯の挨拶を返す。"""
    manager = GreetingManager(str(tmp_path / "greeting.json"))
    manager.mark_active(datetime(2026, 6, 1, 23, 59, tzinfo=timezone.utc))

    assert manager.greeting_on_interaction(datetime(2026, 6, 2, 0, 1, tzinfo=timezone.utc)) == "こんばんは。"


def test_invalid_state_is_treated_as_first_startup(tmp_path):
    """壊れた状態ファイルがあっても起動を継続する。"""
    state_path = tmp_path / "greeting.json"
    state_path.write_text("invalid", encoding="utf-8")
    manager = GreetingManager(str(state_path))

    assert manager.greeting_on_interaction(datetime(2026, 6, 2, 12, tzinfo=timezone.utc)) == "こんにちは。"


def test_time_based_greeting_ranges():
    """朝、昼、夜の境界で挨拶を切り替える。"""
    assert _greeting_for_hour(5) == "おはよう。"
    assert _greeting_for_hour(11) == "こんにちは。"
    assert _greeting_for_hour(18) == "こんばんは。"
