"""フック記録の機微情報と行境界を検証する。"""

import io
import json
import runpy
import sys
from pathlib import Path

import pytest

from argos.services.agent.claude_tmux_events import (
    MAX_EVENT_BYTES, MAX_JOURNAL_BYTES, read_events, record_event,
)
from argos.tools.claude_tmux_hook import main
from argos.tools import claude_tmux_hook


def test_roundtrip(tmp_path: Path) -> None:
    """余分な入力を保存せず、日本語本文と読込位置を維持する。"""
    path = tmp_path / "events"
    assert read_events(path, 0) == ([], 0)
    record_event(path, json.dumps({
        "hook_event_name": "Stop", "session_id": "session", "last_assistant_message": "温泉",
        "secret": "保存しない", "prompt_id": "prompt",
    }))
    events, offset = read_events(path, 0)
    assert events[0].text == "温泉"
    assert offset == path.stat().st_size
    assert read_events(path, offset) == ([], offset)
    assert "secret" not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    with path.open("ab") as output:
        output.write(b"{")
    assert read_events(path, offset) == ([], offset)


@pytest.mark.parametrize("payload", ["[]", json.dumps("a" * MAX_EVENT_BYTES)])
def test_invalid_record(tmp_path: Path, payload: str) -> None:
    """オブジェクト以外と過大な入力を拒否する。"""
    with pytest.raises(ValueError):
        record_event(tmp_path / "events", payload)


def test_full_journal(tmp_path: Path) -> None:
    """ログを無制限に増やさない。"""
    path = tmp_path / "events"
    with path.open("wb") as output:
        output.truncate(MAX_JOURNAL_BYTES + 1)
    with pytest.raises(ValueError, match="16MiB"):
        record_event(path, "{}")


@pytest.mark.parametrize("payload", [b"[]\n", b'{"text":1}\n', b"a" * (MAX_EVENT_BYTES + 1)])
def test_invalid_journal(tmp_path: Path, payload: bytes) -> None:
    """壊れた行を成功の回答へ変換しない。"""
    path = tmp_path / "events"
    path.write_bytes(payload)
    with pytest.raises((ValueError, RuntimeError)):
        read_events(path, 0)


def test_hook_entrypoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """フックのstdoutには回答本文を返さない。"""
    path = tmp_path / "events"
    monkeypatch.setattr(sys, "argv", ["hook", str(path)])
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"last_assistant_message":"非公開"}'))
    main()
    assert capsys.readouterr().out == "{}\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    runpy.run_path(str(claude_tmux_hook.__file__), run_name="__main__")
    assert capsys.readouterr().out == "{}\n"
