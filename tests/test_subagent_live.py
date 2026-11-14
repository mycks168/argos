"""隔離した実CLIで子の完了フックと通常入力の独立を確認する。"""

import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

from argos.config import AgentSlot, load_settings
from argos.services.agent.claude_tmux_client import ClaudeTmuxClient
from argos.services.agent.claude_tmux_session import run_tmux


@pytest.mark.skipif(
    os.environ.get("ARGOS_TEST_SUBAGENTS") != "1",
    reason="実CLIの子起動は明示時だけ実行する",
)
@pytest.mark.parametrize("provider", ["claude", "codex", "antigravity"])
def test_child_completion(tmp_path: Path, provider: str) -> None:
    """親ターン終了後も子の回答を別に取得し、同じ完了を再配信しない。"""
    settings = replace(
        load_settings(),
        agent_state_path=str(tmp_path / "state.json"),
        claude_model="",
        codex_model="",
    )
    extra_args = (
        ("--tools=Agent", "--strict-mcp-config", "--setting-sources=")
        if provider == "claude"
        else ()
    )
    slot = AgentSlot(
        "子エージェント実試験",
        provider,
        "/opt/argos",
        execution_mode="tmux",
        extra_args=extra_args,
        tmux_timeout_seconds=120,
    )
    client = ClaudeTmuxClient(settings, slot)
    try:
        answer = client.ask(
            "Authorized isolated subagent hook test. Create exactly one general-purpose subagent using Agent/spawn_agent/invoke_subagent. Tell it to reply CHILD_RESULT_OK and not use tools or touch files. Return PARENT_RESULT_OK yourself. No confirmation is needed; creation of this one child is explicitly authorized."
        )
        assert answer.strip()
        deadline = time.monotonic() + 60
        while not client.background_ids() and time.monotonic() < deadline:
            time.sleep(0.2)
        ids = client.background_ids()
        assert len(ids) == 1
        with client._exclusive_turn():
            with client.background_answer(ids[0]) as text:
                assert "CHILD_RESULT_OK" in text
        assert client.background_ids() == ()
    finally:
        if client.terminal_session_name:
            run_tmux(["kill-session", "-t", f"={client.terminal_session_name}"])
