from datetime import datetime

import pytest

from pathlib import Path

from claude_usage import (
    _extract_section,
    _parse_pct,
    _parse_reset,
    claude_config_dir,
    list_entries,
    parse_usage,
    remove_new_entries,
    session_log_dir,
)


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


def test_session_log_dir_follows_claude_naming():
    """起動したディレクトリの英数字以外を-にした名前の場所を返す。"""
    assert session_log_dir(Path("/opt/argos/services/agent-limit"), Path("/home/u/.claude")) == Path(
        "/home/u/.claude/projects/-opt-argos-services-agent-limit"
    )
    assert session_log_dir(Path("/a/b.c_d"), Path("/c")) == Path("/c/projects/-a-b-c-d")


def test_claude_config_dir(monkeypatch, tmp_path):
    """CLAUDE_CONFIG_DIRがあればそれを、なければホームの.claudeを使う。"""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert claude_config_dir() == tmp_path
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert claude_config_dir() == tmp_path / "home" / ".claude"


def test_remove_new_entries_keeps_existing(tmp_path):
    """起動前からあった記録は残し、今回できた記録（ファイルとフォルダ）だけを消す。"""
    (tmp_path / "old.jsonl").write_text("{}", encoding="utf-8")
    before = list_entries(tmp_path)
    (tmp_path / "new.jsonl").write_text("{}", encoding="utf-8")
    (tmp_path / "new").mkdir()
    (tmp_path / "new" / "tool.txt").write_text("x", encoding="utf-8")

    assert remove_new_entries(tmp_path, before) == ["new", "new.jsonl"]
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["old.jsonl"]


def test_remove_new_entries_without_directory(tmp_path):
    """記録の場所がまだないときは、何もしない。"""
    missing = tmp_path / "missing"
    assert list_entries(missing) == set()
    assert remove_new_entries(missing, set()) == []


def test_remove_new_entries_skips_undeletable(tmp_path, monkeypatch):
    """消せないものは残し、ほかは消す。"""
    (tmp_path / "a.jsonl").write_text("{}", encoding="utf-8")
    (tmp_path / "b.jsonl").write_text("{}", encoding="utf-8")
    original = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name == "a.jsonl":
            raise OSError("busy")
        return original(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    assert remove_new_entries(tmp_path, set()) == ["b.jsonl"]
    assert (tmp_path / "a.jsonl").exists()
