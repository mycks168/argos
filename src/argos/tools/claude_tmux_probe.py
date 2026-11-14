"""稼働中のARGOS設定を変更せず、Claudeの常駐会話を手動試験する。"""

from __future__ import annotations

import argparse
import math
from dataclasses import replace
from pathlib import Path

from argos.config import AgentSlot, load_settings
from argos.services.agent.claude_tmux_client import ClaudeTmuxClient


def converse(client: ClaudeTmuxClient) -> None:
    """仲介LLMを挟まず、自由文を同じClaudeへ送り続ける。"""
    print("Claude tmux試験です。終了は /quit。ツール実行は無効です。")
    while True:
        try:
            prompt = input("あなた> ")
        except (EOFError, KeyboardInterrupt):
            break
        if prompt.strip() == "/quit":
            break
        if not prompt.strip():
            continue
        try:
            print(f"Claude> {client.ask(prompt)}")
            print(f"端末を別ウインドウで見る: tmux attach-session -t {client.terminal_session_name}")
        except KeyboardInterrupt:
            print("応答待ちを中断しました。Claudeは実行中の可能性があるため再送しません。")
            break
        except (RuntimeError, ValueError, OSError) as error:
            print(f"試験を停止しました: {error}")
            break
    print("会話画面を終了しました。tmuxとClaudeは停止していません。")


def main() -> None:
    """既存スロットと別の台帳を使う1スロットの試験を起動する。"""
    parser = argparse.ArgumentParser(description="Claude常駐tmuxの会話試験")
    parser.add_argument("--name", default="Claude tmux試験")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--state-dir", type=Path, default=Path("~/.local/state/argos/tmux-probe"))
    parser.add_argument("--model", default="")
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    arguments = parser.parse_args()
    if not math.isfinite(arguments.timeout_seconds) or arguments.timeout_seconds < 0:
        parser.error("待ち時間は0以上の有限値にしてください")
    settings = replace(
        load_settings(), agent_state_path=str(arguments.state_dir.expanduser() / "sessions.json"),
        claude_model=arguments.model,
    )
    slot = AgentSlot(
        arguments.name, "claude", str(arguments.cwd.expanduser().resolve()), execution_mode="tmux",
        tmux_timeout_seconds=arguments.timeout_seconds,
        extra_args=("--tools=", "--strict-mcp-config", "--setting-sources="),
    )
    converse(ClaudeTmuxClient(settings, slot))


if __name__ == "__main__":
    main()
