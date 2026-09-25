from datetime import datetime
from pathlib import Path

import pytest

from codex_status import _is_status_ready, parse_status

FIXTURE = (Path(__file__).parent / "fixtures" / "codex_status_screen.txt").read_text()


def test_parse_status_basic() -> None:
    """クレジットを含む標準的な画面を解析する。"""
    now = datetime(2026, 6, 15, 18, 0)
    result = parse_status(FIXTURE, now=now)

    assert result == {
        "five_hour": {"usage_pct": 1, "reset": "06/15 19:26"},
        "weekly": {"usage_pct": 100, "reset": "06/18 20:24"},
        "credits": 882,
    }


def test_five_hour_reset_rolls_over_to_next_day() -> None:
    """短期枠のリセット時刻を過ぎていれば翌日として扱う。"""
    # 5h limitのリセット時刻(19:26)を過ぎている場合は翌日になる
    now = datetime(2026, 6, 15, 20, 0)
    result = parse_status(FIXTURE, now=now)

    assert result["five_hour"]["reset"] == "06/16 19:26"


def test_weekly_reset_rolls_over_to_next_year() -> None:
    """週次枠のリセット日を過ぎていれば翌年として扱う。"""
    # 週次リセット日(6/18)を過ぎている場合は翌年になる
    now = datetime(2026, 12, 31, 0, 0)
    result = parse_status(FIXTURE, now=now)

    assert result["weekly"]["reset"] == "06/18 20:24"
    assert result["weekly"]["reset"].startswith("06/18")


def test_parse_status_raises_on_unexpected_screen() -> None:
    """利用枠がない画面を正常な結果として扱わない。"""
    with pytest.raises(ValueError):
        parse_status("no usage information here")


def test_parse_status_without_five_hour() -> None:
    """短期枠がないモデルでは互換用の値を補う。"""
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


def test_parse_status_without_credits() -> None:
    """クレジット欄がないCodexでも利用枠を取得できる。"""
    screen = """
5h limit:             [██████████████████░░] 90% left (resets 13:31)
Weekly limit:         [████████████████░░░░] 80% left (resets 08:31 on 20 Jul)
"""
    now = datetime(2026, 7, 14, 12, 0)

    result = parse_status(screen, now=now)

    assert result == {
        "five_hour": {"usage_pct": 10, "reset": "07/14 13:31"},
        "weekly": {"usage_pct": 20, "reset": "07/20 08:31"},
    }


def test_status_is_ready_without_credits() -> None:
    """週次枠の完成行があればクレジット表示を待たない。"""
    screen = """
Weekly limit:         [████████████████░░░░] 80% left (resets 08:31 on 20 Jul)
"""

    assert _is_status_ready(screen)


def test_status_is_not_ready_for_incomplete_weekly_row() -> None:
    """週次枠の見出しだけでは取得完了と誤判定しない。"""
    assert not _is_status_ready("Weekly limit:")
