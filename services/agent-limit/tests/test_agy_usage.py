from datetime import datetime
from pathlib import Path

import pytest

from agy_usage import parse_usage

FIXTURE = (Path(__file__).parent / "fixtures" / "agy_usage_screen.txt").read_text()


def test_parse_usage_basic():
    now = datetime(2026, 6, 15, 10, 0)
    result = parse_usage(FIXTURE, now=now)

    assert result == {
        "gemini": {
            "weekly": {"usage_pct": 61.59, "reset": "06/18 23:14"},
            "five_hour": {"usage_pct": 0.15, "reset": "06/15 14:45"},
        },
        "claude_gpt": {
            "weekly": {"usage_pct": 0.0, "reset": None},
            "five_hour": {"usage_pct": 0.0, "reset": None},
        },
    }


def test_parse_usage_raises_on_unexpected_screen():
    with pytest.raises(ValueError):
        parse_usage("no usage information here")
