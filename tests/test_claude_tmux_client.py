"""Claude試験スロットのターン制御と既存経路の接続を検証する。"""

import fcntl
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from argos.config import AgentSlot, Settings, load_settings
from argos.services.agent.claude_tmux_client import ClaudeTmuxClient, claude_command
from argos.services.agent.claude_tmux_events import record_event
from argos.services.agent.claude_tmux_session import ClaudeTmuxState
from argos.services.agent.client import create_provider_client


@pytest.fixture
def client_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ClaudeTmuxClient, Mock, ClaudeTmuxState]:
    """実CLIとスリープを使わない隔離されたスロットを返す。"""
    settings = replace(load_settings(), agent_state_path=str(tmp_path / "state"), claude_model="")
    slot = AgentSlot("試験", "claude", str(tmp_path), execution_mode="tmux", tmux_timeout_seconds=1)
    client = ClaudeTmuxClient(settings, slot)
    session = Mock()
    session.directory = tmp_path
    session.journal_path = tmp_path / "events.jsonl"
    state = ClaudeTmuxState("session", "name", "%27", "hash")
    session.open.return_value = (state, False)
    client._session = session
    client._console_ready_session_id = state.session_id
    monkeypatch.setattr("argos.services.agent.claude_tmux_client.time.sleep", Mock())
    return client, session, state


def append_event(path: Path, event: str, text: str = "", session_id: str = "session") -> None:
    """フック受信を外部プロセスなしで再現する。"""
    record_event(path, json.dumps({"hook_event_name": event, "session_id": session_id, "last_assistant_message": text}))


def test_turn_and_routing(client_setup: tuple[ClaudeTmuxClient, Mock, ClaudeTmuxState]) -> None:
    """貼り付け後の新しいStopだけを既存のaskインターフェースへ返す。"""
    client, session, _state = client_setup
    session.saved_state.return_value = None
    assert client.terminal_session_name == ""
    session.saved_state.return_value = _state
    assert client.terminal_session_name == "name"
    append_event(session.journal_path, "SessionStart")
    append_event(session.journal_path, "Stop", "前回")
    session.transport.return_value.submit = Mock(side_effect=lambda: append_event(session.journal_path, "Stop", "今回"))
    assert "".join(client.ask_slot_stream("試験", "claude", "入力")) == "今回"
    assert client.ask("続き") == "今回"
    assert client.current_provider == "claude"
    assert client.current_model == ""
    assert client.next_slot() == "試験"
    assert not (session.directory / "pending.json").exists()
    with pytest.raises(ValueError, match="一致"):
        client.select_slot("別", "claude")
    client.reset_current()
    session.reset.assert_called_once()


def test_pending_and_lock(client_setup: tuple[ClaudeTmuxClient, Mock, ClaudeTmuxState], monkeypatch: pytest.MonkeyPatch) -> None:
    """中断ターンと別プロセスの実行中ターンへの再送を拒否する。"""
    client, session, _state = client_setup
    (session.directory / "pending.json").write_text("{}")
    with pytest.raises(RuntimeError, match="未確定"):
        client.ask("再送しない")
    monkeypatch.setattr(fcntl, "flock", Mock(side_effect=BlockingIOError))
    with pytest.raises(RuntimeError, match="応答処理中"):
        client.ask("再送しない")
    session.open.assert_not_called()


@pytest.mark.parametrize("event,text", [("StopFailure", ""), ("Stop", " ")])
def test_answer_failure(client_setup: tuple[ClaudeTmuxClient, Mock, ClaudeTmuxState], event: str, text: str) -> None:
    """空のStopや異常終了を正常回答にしない。"""
    client, session, state = client_setup
    append_event(session.journal_path, event, text)
    with pytest.raises(RuntimeError, match="確定できません"):
        client._wait_answer(state, 0)


def test_wait_timeouts(client_setup: tuple[ClaudeTmuxClient, Mock, ClaudeTmuxState], monkeypatch: pytest.MonkeyPatch) -> None:
    """起動待ちと応答待ちの期限を別々に処理する。"""
    client, _session, state = client_setup
    clock = Mock(side_effect=[0, 0, 61])
    monkeypatch.setattr("argos.services.agent.claude_tmux_client.time.monotonic", clock)
    with pytest.raises(RuntimeError, match="起動を確認"):
        client._wait_ready(state)
    monkeypatch.setattr("argos.services.agent.claude_tmux_client.time.monotonic", Mock(side_effect=[0, 0, 2]))
    with pytest.raises(RuntimeError, match="設定時間"):
        client._wait_answer(state, 0)


def test_ignore_other_session(client_setup: tuple[ClaudeTmuxClient, Mock, ClaudeTmuxState], monkeypatch: pytest.MonkeyPatch) -> None:
    """他セッションのStopで回答を終了しない。"""
    client, session, state = client_setup
    append_event(session.journal_path, "Stop", "別", "other")
    session.verify = Mock(side_effect=lambda _state: append_event(session.journal_path, "Stop", "正しい"))
    monkeypatch.setattr(client, "_slot", replace(client._slot, tmux_timeout_seconds=0))
    assert client._wait_answer(state, 0) == "正しい"


def test_command_options(tmp_path: Path) -> None:
    """引数境界を維持し、フックを無効化するオプションを拒否する。"""
    settings = replace(load_settings(), claude_model="sonnet")
    slot = AgentSlot("試験", "claude", str(tmp_path), extra_args=("--tools=",))
    assert claude_command(settings, slot) == (
        "claude", "--permission-mode", "bypassPermissions", "--tools=", "--model", "sonnet",
    )
    with pytest.raises(ValueError, match="上書き"):
        claude_command(settings, replace(slot, extra_args=("--print",)))


def test_factory(tmp_path: Path) -> None:
    """新方式は明示されたローカルClaudeだけへ適用する。"""
    settings: Settings = replace(load_settings(), agent_state_path=str(tmp_path / "state"))
    slot = AgentSlot("試験", "claude", str(tmp_path), execution_mode="tmux")
    assert isinstance(create_provider_client(settings, slot), ClaudeTmuxClient)
    assert isinstance(create_provider_client(settings, replace(slot, provider="codex")), ClaudeTmuxClient)
    with pytest.raises(ValueError, match="ローカルのClaude"):
        create_provider_client(settings, replace(slot, provider="hermes"))


def test_manual_input_and_observation(client_setup: tuple[ClaudeTmuxClient, Mock, ClaudeTmuxState]) -> None:
    """端末入力を再送せず完了フックから読み上げ本文を得る。"""
    client, session, _state = client_setup
    write = Mock()
    client.console_input("会話".encode(), write)
    with pytest.raises(RuntimeError, match="入力中"):
        client.ask("混入しない")
    with pytest.raises(RuntimeError, match="まだ送信"):
        list(client.observe_stream())
    client.console_input(b"\r", write)
    with pytest.raises(RuntimeError, match="応答を待って"):
        client.console_input(b"x", write)
    append_event(session.journal_path, "Stop", "手動の回答")
    assert list(client.observe_stream()) == ["手動の回答"]
    assert not (session.directory / "manual.json").exists()
    session.transport.return_value.send_text.assert_not_called()
    session.transport.return_value.submit.assert_called_once()
    with pytest.raises(RuntimeError, match="送信が見つかりません"):
        list(client.observe_stream())


def test_manual_completion_releases_guard(client_setup: tuple[ClaudeTmuxClient, Mock, ClaudeTmuxState]) -> None:
    """切断中に完了した手動ターンを次の音声回答と取り違えない。"""
    client, session, _state = client_setup
    client.console_input(b"question\r", Mock())
    append_event(session.journal_path, "Stop", "完了")
    client.console_input(b"next", Mock())
    pending = json.loads((session.directory / "manual.json").read_text())
    assert not pending["is_submitted"]
    assert pending["offset"] == session.journal_path.stat().st_size


def test_terminal_reports_during_voice(client_setup: tuple[ClaudeTmuxClient, Mock, ClaudeTmuxState]) -> None:
    """端末の寸法応答や色照会をユーザー入力として扱わない。"""
    client, session, _state = client_setup
    write = Mock()
    with client._exclusive_turn():
        client.console_input(b"\x1b[1;1R", write)
        with pytest.raises(RuntimeError, match="応答処理中"):
            client.console_input(b"x", write)
    write.assert_called_once()
    assert not (session.directory / "manual.json").exists()


def test_discard_manual_draft(client_setup: tuple[ClaudeTmuxClient, Mock, ClaudeTmuxState]) -> None:
    """入力取消後は音声入力へ戻れる。"""
    client, session, _state = client_setup
    client.console_input(b"draft", Mock())
    client.console_input(b"\x15", Mock())
    assert not (session.directory / "manual.json").exists()


def test_manual_input_waits_for_startup(client_setup: tuple[ClaudeTmuxClient, Mock, ClaudeTmuxState]) -> None:
    """描画開始直後の入力をClaudeの初期化中に失わない。"""
    client, session, state = client_setup
    client._console_ready_session_id = ""
    append_event(session.journal_path, "SessionStart")
    write = Mock()
    client.console_input(b"ready", write)
    assert client._console_ready_session_id == state.session_id
    write.assert_called_once()


def test_startup_failure(client_setup: tuple[ClaudeTmuxClient, Mock, ClaudeTmuxState]) -> None:
    """接続確認が失敗した後にユーザー本文を送らない。"""
    client, session, state = client_setup
    append_event(session.journal_path, "StopFailure")
    with pytest.raises(RuntimeError, match="接続確認"):
        client._wait_ready(state)


def test_ready_session_with_later_failure(client_setup: tuple[ClaudeTmuxClient, Mock, ClaudeTmuxState]) -> None:
    """過去のターン失敗を、再接続時の起動失敗と取り違えない。"""
    client, session, state = client_setup
    append_event(session.journal_path, "SessionStart")
    append_event(session.journal_path, "StopFailure")
    client._wait_ready(state)
