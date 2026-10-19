"""フックのCLI入口と、両providerへの追加コンテキストを確認する。"""

import io
import json
import runpy
import sys
from pathlib import Path

import pytest

from argos.services.agent.claude_tmux_events import MAX_EVENT_BYTES


def test_hook_entrypoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """生成したコマンドと同じ引数でモジュールとして起動できる。"""
    (tmp_path / "session.json").write_text('{"session_id":"generation"}')
    monkeypatch.setattr(sys, "argv", ["hook", str(tmp_path), "generation", "codex", "SessionStart"])
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"session_id":"native"}'))
    from argos.tools import tmux_agent_hook

    runpy.run_path(str(tmux_agent_hook.__file__), run_name="__main__")
    output = json.loads(capsys.readouterr().out)
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_hook_agy_and_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """agyには固有のinjectStepsを返し、Stopの標準出力に本文を出さない。"""
    from argos.tools.tmux_agent_hook import main

    (tmp_path / "session.json").write_text('{"session_id":"generation"}')
    monkeypatch.setattr(sys, "argv", ["hook", str(tmp_path), "generation", "antigravity", "SessionStart"])
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"conversationId":"native"}'))
    main()
    assert "injectSteps" in json.loads(capsys.readouterr().out)
    monkeypatch.setattr(sys, "argv", ["hook", str(tmp_path), "generation", "antigravity", "Stop"])
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"conversationId":"native", "error":"failed"}'))
    main()
    assert json.loads(capsys.readouterr().out) == {}


def test_hook_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """巨大なフック入力をJSON解析前に拒否する。"""
    from argos.tools.tmux_agent_hook import main

    monkeypatch.setattr(sys, "argv", ["hook", str(tmp_path), "generation", "codex", "Stop"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * (MAX_EVENT_BYTES + 1)))
    with pytest.raises(ValueError, match="1MiB"):
        main()
