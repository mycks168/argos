"""LLMエージェントの共通クライアント。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from argos.config import Settings
from argos.services.codex.cli import CodexCliClient


class AgentClient(Protocol):
    """ARGOS本体から見たLLMエージェントの共通インターフェース。"""

    @property
    def current_name(self) -> str:
        """現在の会話スロット名を返す。"""

    def next_slot(self) -> str:
        """次の会話スロットへ切り替え、名前を返す。"""

    def reset_current(self) -> None:
        """現在のスロットを新規会話として扱う。"""

    def ask(self, prompt: str) -> str:
        """現在の会話スロットへプロンプトを送り、最終応答を返す。"""

    def ask_stream(self, prompt: str) -> Iterable[str]:
        """現在の会話スロットへプロンプトを送り、応答差分を順に返す。"""


def create_agent_client(settings: Settings) -> AgentClient:
    """設定に応じてLLMエージェントクライアントを作成する。"""
    provider = settings.agent_provider.strip().lower()
    if provider == "codex":
        return CodexCliClient(settings)
    raise ValueError(f"未対応のエージェントプロバイダーです: {settings.agent_provider}")
