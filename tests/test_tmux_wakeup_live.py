"""隔離CLIで親の自律再開と、完了通知の回収状態を観測する。"""

import json
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

from argos.config import AgentSlot, load_settings
from argos.services.agent.claude_tmux_client import ClaudeTmuxClient
from argos.services.agent.claude_tmux_events import read_events
from argos.services.agent.claude_tmux_session import run_tmux

OBSERVATION_TIMEOUT_SECONDS = 75
POLL_INTERVAL_SECONDS = 0.5

PROMPT = """Authorized isolated asynchronous completion test; creating exactly one child is authorized.
Use Agent with run_in_background=true, spawn_agent, or invoke_subagent, as appropriate.
The child must use its shell tool only to run sleep 12, then reply exactly CHILD_FINISHED.
No files, repository content, secrets, network tools, or other tools may be accessed by parent or child.
Immediately after starting the child, finish your first response with exactly PARENT_FIRST.
Do not call wait, wait_agent, or any blocking tool yourself. Do not wait for the child before finishing.
When the child completion notification arrives later, autonomously reply exactly PARENT_LATE.
Do not create another child or ask for confirmation. This is synthetic test data only."""


def collect_child_answers(client: ClaudeTmuxClient, answers: list[str]) -> None:
    """子の完了を通常ターンとは別に回収し、親の通知と混同しない。"""
    for event_id in client.background_ids():
        with client.background_answer(event_id) as answer:
            answers.append(answer)


@pytest.mark.skipif(os.environ.get("ARGOS_TEST_WAKEUP") != "1", reason="実CLIの非同期試験は明示時だけ実行する")
@pytest.mark.parametrize("provider", ["claude", "codex", "antigravity"])
def test_observe_parent_wakeup(tmp_path: Path, provider: str) -> None:
    """発火しなかった場合も隠さず、親StopとARGOSの回収対象を分けて報告する。"""
    settings = replace(load_settings(), agent_state_path=str(tmp_path / "state.json"), claude_model="", codex_model="")
    extra_args = ("--tools=Agent,Bash", "--strict-mcp-config", "--setting-sources=") if provider == "claude" else ()
    slot = AgentSlot("自律再開の隔離試験", provider, "/opt/argos", execution_mode="tmux", extra_args=extra_args, tmux_timeout_seconds=120)
    client = ClaudeTmuxClient(settings, slot)
    try:
        state = client.terminal_target()
        client._wait_ready(state)
        offset = client._session.journal_path.stat().st_size
        first_answer = client.ask(PROMPT)
        child_answers: list[str] = []
        deadline = time.monotonic() + OBSERVATION_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            collect_child_answers(client, child_answers)
            events, _position = read_events(client._session.journal_path, offset)
            if child_answers and any(event.event == "Stop" and "PARENT_LATE" in event.text for event in events):
                break
            time.sleep(POLL_INTERVAL_SECONDS)
        events, _position = read_events(client._session.journal_path, offset)
        report = {
            "provider":provider,
            "first_returned_before_late":"PARENT_FIRST" in first_answer and "PARENT_LATE" not in first_answer,
            "parent_stop_texts":[event.text for event in events if event.event == "Stop"],
            "child_finished":any("CHILD_FINISHED" in answer for answer in child_answers),
            "has_pending_parent":client.has_pending_manual_turn(),
            "remaining_background_ids":len(client.background_ids()),
        }
        (tmp_path / "wakeup-report.json").write_text(json.dumps(report, ensure_ascii=False))
        print(json.dumps(report, ensure_ascii=False), flush=True)
        assert first_answer.strip()
    finally:
        if client.terminal_session_name:
            run_tmux(["kill-session", "-t", f"={client.terminal_session_name}"])
