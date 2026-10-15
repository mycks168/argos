"""常駐起動引数とworkspaceフックの隔離条件を確認する。"""

import json
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from argos.config import AgentSlot, load_settings
from argos.services.agent.tmux_commands import agy_environment, codex_hooks, interactive_command
from argos.services.agent.claude_tmux_client import ClaudeTmuxClient


def test_codex_command(tmp_path: Path) -> None:
    """非対話・再開引数を拒否し、サブスクリプションの対話CLIを使う。"""
    settings = replace(load_settings(), codex_model="test-model", codex_extra_args=(), codex_bypass_sandbox=False)
    slot = AgentSlot("試験", "codex", str(tmp_path))
    command = interactive_command(settings, slot)
    assert "--no-daemon" in command and "never" in command and "test-model" in command
    assert "--dangerously-bypass-approvals-and-sandbox" in interactive_command(replace(settings, codex_bypass_sandbox=True), slot)
    client = ClaudeTmuxClient(replace(settings, codex_home=str(tmp_path), agent_state_path=str(tmp_path / "state")), slot)
    assert client._session._spec.environment == (("CODEX_HOME", str(tmp_path)),)
    for argument in ["exec", "--print", "--conversation=other"]:
        with pytest.raises(ValueError):
            interactive_command(settings, replace(slot, extra_args=(argument,)))
    with pytest.raises(ValueError):
        interactive_command(settings, replace(slot, provider="hermes"))


def test_agy_command(tmp_path: Path) -> None:
    """agyの権限・sandboxと設定済みモデルを引き継ぐ。"""
    settings = replace(load_settings(), antigravity_model="", antigravity_extra_args=(), antigravity_skip_permissions=True, antigravity_sandbox=True)
    command = interactive_command(settings, AgentSlot("試験", "antigravity", str(tmp_path)))
    assert "--dangerously-skip-permissions" in command and "--sandbox" in command


def test_codex_inline_hooks(tmp_path: Path) -> None:
    """JSONではなく有効なTOMLとしてCLIへ渡す。"""
    arguments = codex_hooks(tmp_path, "generation")
    for index in range(0, len(arguments), 2):
        assert arguments[index] == "--config"
        assert "hooks" in tomllib.loads(arguments[index + 1])


def test_agy_environment(tmp_path: Path) -> None:
    """未導入のworkspaceを勝手に変更せず、明示的な設定を要求する。"""
    with pytest.raises(ValueError, match="workspace"):
        agy_environment(tmp_path, "generation", str(tmp_path))
    hooks = tmp_path / ".agents"
    hooks.mkdir()
    (hooks / "hooks.json").write_text(json.dumps({"argos-tmux": {}}))
    values = agy_environment(tmp_path, "generation", str(tmp_path))
    assert f"ARGOS_TMUX_HOOK_DIRECTORY={tmp_path}" in values
