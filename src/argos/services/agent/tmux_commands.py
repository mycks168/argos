"""対話型Codex・agyの起動条件を会話の排他制御から分離する。"""

import json
import shlex
import sys
from pathlib import Path

from argos.config import AgentSlot, Settings, resolve_agent_slot_command, resolve_agent_slot_extra_args, resolve_agent_slot_model


def interactive_command(settings: Settings, slot: AgentSlot) -> tuple[str, ...]:
    """非対話モードや会話の上書きを許さず、既存の権限設定を引き継ぐ。"""
    arguments = (*shlex.split(resolve_agent_slot_command(settings, slot)), *resolve_agent_slot_extra_args(settings, slot))
    reserved = {"exec", "resume", "--remote", "--session-id", "--conversation", "--continue", "-c", "-p", "--print",
                "--prompt", "-i", "--prompt-interactive", "--input-format", "--output-format", "--json", "--new-project", "--worktree"}
    if any(argument.split("=", 1)[0] in reserved for argument in arguments[1:]):
        raise ValueError("tmuxスロットでは非対話・会話再開・設定上書きの引数は使えません")
    provider = slot.provider.strip().lower()
    if provider == "codex":
        permissions = ("--dangerously-bypass-approvals-and-sandbox",) if settings.codex_bypass_sandbox else (
            "--sandbox", settings.codex_sandbox, "--ask-for-approval", "never")
        arguments += (*permissions, "--no-daemon", "--no-alt-screen", "--dangerously-bypass-hook-trust")
    elif provider == "antigravity":
        if settings.antigravity_skip_permissions:
            arguments += ("--dangerously-skip-permissions",)
        if settings.antigravity_sandbox:
            arguments += ("--sandbox",)
    else:
        raise ValueError("対話型アダプターに未対応のproviderです")
    model = resolve_agent_slot_model(settings, slot)
    return (*arguments, "--model", model) if model else arguments


def hook_command(directory: Path, generation: str, provider: str, event: str) -> str:
    """対象と世代を固定し、共通のフック記録へ接続する。"""
    return shlex.join([sys.executable, "-m", "argos.tools.tmux_agent_hook", str(directory), generation, provider, event])


def codex_hooks(directory: Path, generation: str) -> tuple[str, ...]:
    """ユーザーのconfig.tomlは変更せず、この起動だけにフックを指定する。"""
    arguments: list[str] = []
    for event in ("SessionStart", "Stop", "Interrupt"):
        handlers = [{"hooks": [{"type": "command", "command": hook_command(directory, generation, "codex", event)}]}]
        encoded_command = json.dumps(handlers[0]["hooks"][0]["command"])
        arguments.extend(["--config", f'hooks.{event}=[{{hooks=[{{type="command",command={encoded_command}}}]}}]'])
    return tuple(arguments)


def agy_environment(directory: Path, generation: str, cwd: str) -> tuple[str, ...]:
    """共通フックが他のagy起動を記録しないよう、当該tmuxだけへ識別値を渡す。"""
    hook_path = Path(cwd) / ".agents" / "hooks.json"
    if not hook_path.is_file() or "argos-tmux" not in json.loads(hook_path.read_text()):
        raise ValueError(f"agyのtmux用workspaceフックが必要です: {hook_path}")
    return ("-e", f"ARGOS_TMUX_HOOK_DIRECTORY={directory}", "-e", f"ARGOS_TMUX_GENERATION={generation}",
            "-e", f"ARGOS_TMUX_PYTHON={sys.executable}")
