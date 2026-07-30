from datetime import datetime
from pathlib import Path

import pytest

from codex_status import parse_status

FIXTURE = (Path(__file__).parent / "fixtures" / "codex_status_screen.txt").read_text()


def test_parse_status_basic():
    now = datetime(2026, 6, 15, 18, 0)
    result = parse_status(FIXTURE, now=now)

    assert result == {
        "five_hour": {"usage_pct": 1, "reset": "06/15 19:26"},
        "weekly": {"usage_pct": 100, "reset": "06/18 20:24"},
        "credits": 882,
    }


def test_five_hour_reset_rolls_over_to_next_day():
    # 5h limitのリセット時刻(19:26)を過ぎている場合は翌日になる
    now = datetime(2026, 6, 15, 20, 0)
    result = parse_status(FIXTURE, now=now)

    assert result["five_hour"]["reset"] == "06/16 19:26"


def test_weekly_reset_rolls_over_to_next_year():
    # 週次リセット日(6/18)を過ぎている場合は翌年になる
    now = datetime(2026, 12, 31, 0, 0)
    result = parse_status(FIXTURE, now=now)

    assert result["weekly"]["reset"] == "06/18 20:24"
    assert result["weekly"]["reset"].startswith("06/18")


def test_parse_status_raises_on_unexpected_screen():
    with pytest.raises(ValueError):
        parse_status("no usage information here")


def test_parse_status_without_five_hour():
    screen = """
Weekly limit:         [██████████████████░░] 90% left (resets 08:31 on 20 Jul)
Credits:              794 credits
"""
    now = datetime(2026, 7, 14, 12, 0)
    result = parse_status(screen, now=now)
    assert result == {
        "five_hour": {"usage_pct": 0, "reset": "N/A"},
        "weekly": {"usage_pct": 10, "reset": "07/20 08:31"},
        "credits": 794,
    }
