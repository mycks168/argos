from datetime import datetime
from collections.abc import Callable
from pathlib import Path

import pytest
import codex_status

from codex_status import _is_status_ready, parse_limit_row, parse_status

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


@pytest.mark.parametrize(
    ("clock", "expected"),
    [("9:41 PM", "21:41"), ("4:41 PM", "16:41"),
     ("12:00 AM", "00:00"), ("12:00 PM", "12:00"),
     ("9:41 am", "09:41"), ("21:41", "21:41"), ("9:41", "09:41")],
)
def test_limit_row_accepts_both_clock_formats(clock: str, expected: str) -> None:
    """新旧の時刻表記と正午・深夜を同じ24時間表記へ正規化する。"""
    screen = f"Weekly limit: [████] 99% left (resets {clock} on 20 Oct)"
    result = parse_limit_row("Weekly limit", screen, datetime(2026, 10, 13, 17))
    assert result == {"usage_pct": 1, "reset": f"10/20 {expected}"}
    assert _is_status_ready(screen)


@pytest.mark.parametrize("clock", ["0:00 AM", "13:00 PM", "24:00", "12:60"])
def test_limit_row_rejects_invalid_clock(clock: str) -> None:
    """不正な時刻を完成したステータスとして扱わない。"""
    screen = f"Weekly limit: [████] 99% left (resets {clock} on 20 Oct)"
    assert not _is_status_ready(screen)


def test_parse_current_status_without_credits() -> None:
    """実機の12時間表記をクレジットなしでも完了として解析する。"""
    screen = """
5h limit: [████] 91% left (resets 9:41 PM)
Weekly limit: [████] 99% left (resets 4:41 PM on 20 Oct)
"""
    result = parse_status(screen, now=datetime(2026, 10, 13, 17))
    assert result == {
        "five_hour": {"usage_pct": 9, "reset": "10/13 21:41"},
        "weekly": {"usage_pct": 1, "reset": "10/20 16:41"},
    }


@pytest.mark.parametrize("startup", ["OpenAI Codex", "Do you trust", "Update available"])
def test_run_once_uses_weekly_completion_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, startup: str
) -> None:
    """起動確認後に12時間表記で取得を完了し、一時セッションを閉じる。"""
    final_screen = "Weekly limit: [████] 99% left (resets 4:41 PM on 20 Oct)"
    screens = [startup]
    if startup == "Update available":
        screens.append("Do you trust")
    if startup != "OpenAI Codex":
        screens.append("OpenAI Codex")
    screens.extend(["show current session configuration", final_screen])
    cleaned_sessions: list[str] = []

    def fake_wait_for(
        session: str, predicate: Callable[[str], bool], timeout: float = 30
    ) -> str:
        """一時セッションへの操作を模擬する。"""
        assert session.startswith("codex_status_")
        assert timeout == 60
        screen = screens.pop(0)
        assert predicate(screen)
        return screen

    monkeypatch.setattr(codex_status, "wait_for", fake_wait_for)
    monkeypatch.setattr(codex_status, "tmux", lambda *_args: None)
    monkeypatch.setattr(codex_status, "send_keys", lambda *_keys: None)
    monkeypatch.setattr(codex_status.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(codex_status, "cleanup", cleaned_sessions.append)
    assert codex_status.run_once()["weekly"]["usage_pct"] == 1
    assert len(cleaned_sessions) == 1


def test_refresh_requested_is_ready() -> None:
    """再取得要求は完了扱いで検出し、呼び出し元へ再試行判断を渡す。"""
    assert _is_status_ready("refresh requested")
