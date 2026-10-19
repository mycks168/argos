"""明示時だけCodex・agyの実CLIで常駐会話を確認する。"""

import os
from dataclasses import replace
from pathlib import Path

import pytest

from argos.config import AgentSlot, load_settings
from argos.services.agent.claude_tmux_client import ClaudeTmuxClient
from argos.services.agent.claude_tmux_session import run_tmux


@pytest.mark.skipif(os.environ.get("ARGOS_TEST_TMUX_AGENTS") != "1", reason="実CLIは明示時だけ実行する")
@pytest.mark.parametrize("provider", ["codex", "antigravity"])
def test_persistent_agents(tmp_path: Path, provider: str) -> None:
    """クライアントを再生成しても別ターンの回答を取り違えず会話を続ける。"""
    settings = replace(load_settings(), agent_state_path=str(tmp_path / "state.json"),
                       codex_model="", antigravity_model="", codex_extra_args=(), antigravity_extra_args=())
    slot = AgentSlot("常駐CLI実試験", provider, "/opt/argos", execution_mode="tmux", tmux_timeout_seconds=120)
    client = ClaudeTmuxClient(settings, slot)
    try:
        answer = client.ask("ツールを使わない会話の動作確認です。合言葉は『黄色い星』です。覚えて短く返事してください。")
        assert answer.strip()
        state = client.terminal_target()
        resumed = ClaudeTmuxClient(settings, slot)
        assert resumed.terminal_target().session_id == state.session_id
        assert "黄色い星" in resumed.ask("ツールは使わず、先ほどの合言葉だけ答えてください。")
        question = resumed.ask("ツールや選択メニューは使わず、お茶とコーヒーのどちらにするか普通の文章で質問して返事を待ってください。")
        assert "お茶" in question and "コーヒー" in question
        assert "水" in resumed.ask("どちらでもなく水にします。私が選んだ飲み物だけ答えてください。")
    finally:
        if client.terminal_session_name:
            run_tmux(["kill-session", "-t", f"={client.terminal_session_name}"])
