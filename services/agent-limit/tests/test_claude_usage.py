from datetime import datetime

import pytest

from claude_usage import _extract_section, _parse_pct, _parse_reset, parse_usage


CLAUDE_USAGE_SCREEN = """
Current session
  Usage
    12.34% used
  Resets 2:45pm (local)

Current week
  Usage
    56.78% used
  Resets Jun 18, 11:14pm (local)

Usage credits
"""


def test_parse_usage_basic():
    """claudeの/usage画面からセッションと週次の使用率を解析する。"""
    now = datetime(2026, 6, 15, 10, 0)

    assert parse_usage(CLAUDE_USAGE_SCREEN, now=now) == {
        "weekly": {"usage_pct": 56.78, "reset": "06/18 23:14"},
        "five_hour": {"usage_pct": 12.34, "reset": "06/15 14:45"},
    }


def test_parse_usage_raises_on_unexpected_screen():
    """想定外の画面では解析失敗にする。"""
    with pytest.raises(ValueError):
        parse_usage("no usage information here")


def test_parse_reset_variants():
    """claudeのリセット時刻表記ゆれを解析する。"""
    now = datetime(2026, 6, 15, 10, 0)

    assert _parse_reset("Resets 12:05am", now) == "06/15 00:05"
    assert _parse_reset("Resets 2pm", now) == "06/15 14:00"
    assert _parse_reset("Resets 12am", now) == "06/15 00:00"
    assert _parse_reset("Resets Foo 28, 12am", now) == "06/28 00:00"
    assert _parse_reset("Resets later", now) == "later"
    assert _parse_reset("No reset information", now) is None


def test_extract_section_and_pct_fallbacks():
    """セクション切り出しと使用率未検出時のフォールバックを確認する。"""
    assert _extract_section("abc", "missing", "next") == ""
    assert _extract_section("Current session only", "Current session", "Current week") == "Current session only"
    assert _parse_pct("no percent") == 0.0
