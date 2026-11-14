"""明示した場合だけサブスクリプションのClaudeで常駐会話を試験する。"""

import os
from dataclasses import replace
from pathlib import Path

import pytest

from argos.config import AgentSlot, load_settings
from argos.services.agent.claude_tmux_client import ClaudeTmuxClient
from argos.services.agent.claude_tmux_session import run_tmux


@pytest.mark.skipif(os.environ.get("ARGOS_TEST_CLAUDE_LIVE") != "1", reason="明示時だけClaudeを実行する")
def test_claude_persistent_conversation(tmp_path: Path) -> None:
    """クライアントを作り直しても同じClaudeが前の発言を覚えている。"""
    settings = replace(load_settings(), agent_state_path=str(tmp_path / "sessions.json"), claude_model="")
    slot = AgentSlot(
        "tmux試験", "claude", "/opt/argos", execution_mode="tmux", tmux_timeout_seconds=90,
        extra_args=("--tools=", "--strict-mcp-config", "--setting-sources="),
    )
    first = ClaudeTmuxClient(settings, slot)
    try:
        response = first.ask("動作試験です。合言葉は『青い温泉』です。覚えて、日本語で短く返事してください。")
        assert response.strip()
        second = ClaudeTmuxClient(settings, slot)
        response = second.ask("さっき伝えた合言葉だけを答えてください。")
        assert "青い温泉" in response
        question = second.ask("お茶とコーヒーのどちらを飲むか、私に普通の文章で質問して返事を待ってください。")
        assert "お茶" in question and "コーヒー" in question
        response = second.ask("どちらでもなく、水にします。私が選んだ飲み物だけ答えてください。")
        assert "水" in response
    finally:
        state, _is_created = first._session.open()
        run_tmux(["kill-session", "-t", f"={state.session_name}"])
