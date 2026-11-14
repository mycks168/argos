"""常駐プロセスと保存済みスロットの対応を検証する。"""

import json
import subprocess
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from argos.services.agent.claude_tmux_session import (
    ClaudeTmuxSession, ClaudeTmuxSpec, ClaudeTmuxState, run_tmux, write_private_json,
)


def test_create_reuse_and_config_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """別クライアントから同じ保存情報へ再接続する。"""
    command = Mock(return_value="%27")
    monkeypatch.setattr("argos.services.agent.claude_tmux_session.run_tmux", command)
    spec = ClaudeTmuxSpec("試験", str(tmp_path), ("claude",))
    session = ClaudeTmuxSession(tmp_path, spec)
    state, is_created = session.open()
    assert is_created
    assert command.call_args.args[0][-1].startswith("ユーザーへの")
    assert "--disallowedTools=AskUserQuestion" in command.call_args.args[0]
    assert (tmp_path / "session.json").stat().st_mode & 0o777 == 0o600
    assert "StopFailure" in json.loads((tmp_path / "hooks.json").read_text())["hooks"]
    command.return_value = f"{state.session_name}:0"
    assert ClaudeTmuxSession(tmp_path, spec).open() == (state, False)
    assert session.transport(state)._pane_target == "%27"
    changed = ClaudeTmuxSession(tmp_path, replace(spec, command=("claude", "--model", "sonnet")))
    with pytest.raises(RuntimeError, match="設定が変わって"):
        changed.open()
    command.return_value = "別セッション:0"
    with pytest.raises(RuntimeError, match="終了または変更"):
        session.verify(state)


def test_codex_launch_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """設定されたCODEX_HOMEを当該セッションだけへ引き継ぐ。"""
    command = Mock(return_value="%27")
    monkeypatch.setattr("argos.services.agent.claude_tmux_session.run_tmux", command)
    spec = ClaudeTmuxSpec("試験", str(tmp_path), ("codex",), "codex", (("CODEX_HOME", str(tmp_path)),))
    session = ClaudeTmuxSession(tmp_path, spec)
    session.open()
    assert f"CODEX_HOME={tmp_path}" in command.call_args.args[0]
    assert spec.fingerprint != replace(spec, environment=()).fingerprint


def test_agy_launch_environment(tmp_path: Path) -> None:
    """最初の接続確認だけを起動時に送り、識別値は当該セッションへ限定する。"""
    hook_directory = tmp_path / ".agents"
    hook_directory.mkdir()
    (hook_directory / "hooks.json").write_text('{"argos-tmux":{}}')
    session = ClaudeTmuxSession(tmp_path, ClaudeTmuxSpec("試験", str(tmp_path), ("agy",), "antigravity"))
    command, environment = session._launch_arguments("generation")
    assert "--prompt-interactive" in command
    assert "ARGOS_TMUX_GENERATION=generation" in environment


@pytest.mark.parametrize("failure", [OSError("secret"), subprocess.TimeoutExpired("tmux", 5)])
def test_process_error(monkeypatch: pytest.MonkeyPatch, failure: Exception) -> None:
    """外部呼び出しの例外を日本語の文脈へ変換する。"""
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=failure))
    with pytest.raises(RuntimeError, match="operation=display-message"):
        run_tmux(["display-message"])


def test_process_exit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """終了コードが失敗の場合はstdoutを成功値として返さない。"""
    monkeypatch.setattr(subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 1, "secret")))
    with pytest.raises(RuntimeError, match="操作に失敗"):
        run_tmux(["display-message"])
    monkeypatch.setattr(subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0, " %27\n")))
    assert run_tmux(["display-message"]) == "%27"


def test_private_replacement(tmp_path: Path) -> None:
    """台帳の置換後に中間ファイルを残さない。"""
    path = tmp_path / "state.json"
    state = ClaudeTmuxState("session", "name", "%27", "hash")
    write_private_json(path, json.dumps(asdict(state)))
    assert json.loads(path.read_text())["pane_id"] == "%27"
    assert not path.with_suffix(".tmp").exists()


@pytest.mark.parametrize("content", ["[]", '{"pane_id":3}', "{}", "not-json"])
def test_corrupt_state(tmp_path: Path, content: str) -> None:
    """破損した台帳を無視して別のClaudeを起動しない。"""
    (tmp_path / "session.json").write_text(content)
    session = ClaudeTmuxSession(tmp_path, ClaudeTmuxSpec("試験", str(tmp_path), ("claude",)))
    with pytest.raises(RuntimeError, match="台帳を読み込めません"):
        session.open()


def test_wrong_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """対応を書き換えられた場合は別セッションへアクセスしない。"""
    monkeypatch.setattr("argos.services.agent.claude_tmux_session.run_tmux", Mock(return_value="%27"))
    session = ClaudeTmuxSession(tmp_path, ClaudeTmuxSpec("試験", str(tmp_path), ("claude",)))
    state, _is_created = session.open()
    path = tmp_path / "session.json"
    write_private_json(path, json.dumps(asdict(replace(state, session_name="別の作業"))))
    with pytest.raises(RuntimeError, match="台帳を読み込めません"):
        session.saved_state()


@pytest.mark.parametrize("has_journal", [True, False])
def test_reset_owned_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, has_journal: bool) -> None:
    """リセットで他のセッションを消さず、過去の回答記録を残す。"""
    command = Mock(return_value="%27")
    monkeypatch.setattr("argos.services.agent.claude_tmux_session.run_tmux", command)
    session = ClaudeTmuxSession(tmp_path, ClaudeTmuxSpec("試験", str(tmp_path), ("claude",)))
    session.reset()
    command.assert_not_called()
    state, _is_created = session.open()
    command.return_value = f"{state.session_name}:0"
    (tmp_path / "pending.json").write_text("{}")
    if has_journal:
        session.journal_path.write_text("以前の回答")
    session.reset()
    assert command.call_args.args[0] == ["kill-session", "-t", f"={state.session_name}"]
    assert session.saved_state() is None
    assert not (tmp_path / "pending.json").exists()
    if has_journal:
        assert (tmp_path / f"events.{state.session_id}.jsonl").read_text() == "以前の回答"
