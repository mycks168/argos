"""隔離した実CLIで、親の後続回答がRunnerの未配信ジョブになることを確認する。"""

import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

from argos.config import AgentSlot, load_settings
from argos.services.agent.claude_tmux_client import ClaudeTmuxClient
from argos.services.agent.claude_tmux_session import run_tmux
from argos.services.agent.runner import AgentJobStore
from argos.services.agent.tmux_reply_collector import TmuxReplyCollector
from test_tmux_wakeup_live import PROMPT

COLLECTION_TIMEOUT_SECONDS = 90
POLL_INTERVAL_SECONDS = 0.5


@pytest.mark.skipif(os.environ.get("ARGOS_TEST_REPLY_COLLECTION") != "1", reason="実CLIと子の起動は明示時だけ行う")
@pytest.mark.parametrize("provider", ["claude", "codex", "antigravity"])
def test_parent_wakeup_reaches_runner(tmp_path: Path, provider: str) -> None:
    """通常回答を重複登録せず、子と自律再開した親を耐久的に回収する。"""
    slot = AgentSlot("Runner後続回答の隔離試験", provider, "/opt/argos", execution_mode="tmux", tmux_timeout_seconds=120,
        extra_args=("--tools=Agent,Bash", "--strict-mcp-config", "--setting-sources=") if provider == "claude" else ())
    settings = replace(load_settings(), agent_slots=(slot,), agent_state_path=str(tmp_path / "state.json"),
        agent_runner_state_dir=str(tmp_path / "runner"), claude_model="", codex_model="")
    client = ClaudeTmuxClient(settings, slot)
    store = AgentJobStore(Path(settings.agent_runner_state_dir))
    collector = TmuxReplyCollector(settings, store)
    try:
        first = client.ask(PROMPT)
        assert "PARENT_FIRST" in first and "PARENT_LATE" not in first
        deadline = time.monotonic() + COLLECTION_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            collector.poll()
            replies = [Path(job.result_path).read_text() for job in store.list_undelivered()]
            has_child = any("CHILD_FINISHED" in reply for reply in replies)
            has_parent = any("PARENT_LATE" in reply for reply in replies)
            if has_child and (has_parent or provider == "codex"):
                break
            time.sleep(POLL_INTERVAL_SECONDS)
        assert has_child
        if provider != "codex":
            assert has_parent
        assert not any("PARENT_FIRST" in reply for reply in replies)
        before = [job.job_id for job in store.list_undelivered()]
        TmuxReplyCollector(settings, store).poll()
        assert [job.job_id for job in store.list_undelivered()] == before
        print(f"{provider}: child={has_child}, late_parent={has_parent}, jobs={len(before)}, normal_duplicate=False", flush=True)
    finally:
        if client.terminal_session_name:
            run_tmux(["kill-session", "-t", f"={client.terminal_session_name}"])
