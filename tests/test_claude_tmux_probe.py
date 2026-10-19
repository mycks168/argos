"""手動試験コマンドが既存設定を変更しないことを検証する。"""

import runpy
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from argos.tools import claude_tmux_probe


@pytest.mark.parametrize("exit_input", ["/quit", EOFError(), KeyboardInterrupt()])
def test_conversation_exit(monkeypatch: pytest.MonkeyPatch, exit_input: str | BaseException) -> None:
    """終了時はClaudeを強制終了せず、空入力も送信しない。"""
    client = Mock()
    client.ask.return_value = "返事"
    monkeypatch.setattr("builtins.input", Mock(side_effect=["", "自由な返事", exit_input]))
    claude_tmux_probe.converse(client)
    client.ask.assert_called_once_with("自由な返事")


@pytest.mark.parametrize("error", [RuntimeError("中断"), KeyboardInterrupt()])
def test_conversation_error(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    """失敗した会話を自動で再送しない。"""
    client = Mock(ask=Mock(side_effect=error))
    monkeypatch.setattr("builtins.input", Mock(return_value="入力"))
    claude_tmux_probe.converse(client)
    client.ask.assert_called_once()


def test_probe_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """利用中のARGOSではなく隔離された試験クライアントを作る。"""
    factory = Mock()
    monkeypatch.setattr("argos.services.agent.claude_tmux_client.ClaudeTmuxClient", factory)
    monkeypatch.setattr("builtins.input", Mock(return_value="/quit"))
    monkeypatch.setattr(sys, "argv", ["probe", "--state-dir", str(tmp_path)])
    runpy.run_path(str(claude_tmux_probe.__file__), run_name="__main__")
    settings, slot = factory.call_args.args
    assert settings.agent_state_path == str(tmp_path / "sessions.json")
    assert slot.execution_mode == "tmux"
    assert "--tools=" in slot.extra_args


def test_invalid_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """不正な待ち時間では実プロセスを起動しない。"""
    monkeypatch.setattr(sys, "argv", ["probe", "--timeout-seconds", "-1"])
    with pytest.raises(SystemExit):
        claude_tmux_probe.main()
