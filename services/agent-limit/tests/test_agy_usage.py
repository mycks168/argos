from datetime import datetime
from pathlib import Path

import pytest

from agy_usage import _wait_until_ready, parse_usage

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


def test_parse_usage_with_remaining_label():
    screen = """
GEMINI MODELS
  Models within this group: Gemini Flash, Gemini Pro

  Weekly Limit Remaining
    [█████████████████████████████████████████████████░] 98.95%
    99% remaining · Refreshes in 80h 56m

  Five Hour Limit Remaining
    [█████████████████████████████████████████████████░] 98.20%
    98% remaining · Refreshes in 4h 59m

CLAUDE AND GPT MODELS
  Models within this group: Claude 3.5 Sonnet, Claude 3.7 Sonnet

  Weekly Limit Remaining
    [██████████████████████████████████████████████████] 100.00%
    100% remaining · Refreshes in 163h 3m

  Five Hour Limit Remaining
    [██████████████████████████████████████████████████] 100.00%
    Quota available
"""
    now = datetime(2026, 8, 27, 10, 0)
    result = parse_usage(screen, now=now)
    assert result["gemini"]["weekly"]["usage_pct"] == 1.05
    assert result["gemini"]["weekly"]["reset"] == "08/30 18:56"
    assert result["gemini"]["five_hour"]["usage_pct"] == 1.8
    assert result["gemini"]["five_hour"]["reset"] == "08/27 14:59"
    assert result["claude_gpt"]["weekly"]["usage_pct"] == 0.0
    assert result["claude_gpt"]["weekly"]["reset"] == "09/03 05:03"
    assert result["claude_gpt"]["five_hour"]["usage_pct"] == 0.0
    assert result["claude_gpt"]["five_hour"]["reset"] is None


def test_parse_usage_raises_on_unexpected_screen():
    with pytest.raises(ValueError):
        parse_usage("no usage information here")


def test_wait_until_ready_accepts_trust_confirmation(monkeypatch):
    """agy初回起動の信頼確認を通してから準備完了を待つ。"""
    screens = iter(["Do you trust this folder?", "Press ? for shortcuts"])
    sent = []

    def fake_wait_for(_session, predicate, timeout=30, interval=0.5):
        text = next(screens)
        assert predicate(text)
        return text

    monkeypatch.setattr("agy_usage.wait_for", fake_wait_for)
    monkeypatch.setattr("agy_usage.send_keys", lambda session, *keys: sent.append((session, keys)))
    monkeypatch.setattr("agy_usage.time.sleep", lambda _seconds: None)

    assert _wait_until_ready("agy-test") == "Press ? for shortcuts"
    assert sent == [("agy-test", ("Enter",))]
