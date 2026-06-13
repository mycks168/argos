"""LLMエージェントの共通クライアント。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Protocol

from argos.config import AgentSlot
from argos.config import Settings
from argos.services.antigravity import AntigravityCliClient
from argos.services.codex.cli import CodexCliClient
from argos.services.hermes import HermesCliClient


class AgentClient(Protocol):
    """ARGOS本体から見たLLMエージェントの共通インターフェース。"""

    @property
    def current_name(self) -> str:
        """現在の会話スロット名を返す。"""

    @property
    def current_provider(self) -> str:
        """現在の会話スロットのprovider名を返す。"""

    def next_slot(self) -> str:
        """次の会話スロットへ切り替え、名前を返す。"""

    def reset_current(self) -> None:
        """現在のスロットを新規会話として扱う。"""

    def ask(self, prompt: str) -> str:
        """現在の会話スロットへプロンプトを送り、最終応答を返す。"""

    def ask_stream(self, prompt: str) -> Iterable[str]:
        """現在の会話スロットへプロンプトを送り、応答差分を順に返す。"""


@dataclass
class AgentRoute:
    """1つのスロットと対応するproviderクライアント。"""

    slot: AgentSlot
    client: AgentClient


class RoutedAgentClient:
    """スロットごとにproviderクライアントへ振り分ける。"""

    def __init__(self, settings: Settings) -> None:
        """設定から各スロットのproviderクライアントを作成する。"""
        self._routes = [AgentRoute(slot=slot, client=create_provider_client(settings, slot)) for slot in settings.agent_slots]
        if not self._routes:
            raise ValueError("エージェントスロットが設定されていません")
        self._index = 0

    @property
    def current_name(self) -> str:
        """現在の会話スロット名を返す。"""
        return self._routes[self._index].slot.name

    @property
    def current_provider(self) -> str:
        """現在の会話スロットのprovider名を返す。"""
        return self._routes[self._index].slot.provider

    def next_slot(self) -> str:
        """次の会話スロットへ切り替え、名前を返す。"""
        self._index = (self._index + 1) % len(self._routes)
        return self.current_name

    def reset_current(self) -> None:
        """現在のスロットを新規会話として扱う。"""
        self._routes[self._index].client.reset_current()

    def ask(self, prompt: str) -> str:
        """現在の会話スロットへプロンプトを送り、最終応答を返す。"""
        return self._routes[self._index].client.ask(prompt)

    def ask_stream(self, prompt: str) -> Iterable[str]:
        """現在の会話スロットへプロンプトを送り、応答差分を順に返す。"""
        return self._routes[self._index].client.ask_stream(prompt)


def create_agent_client(settings: Settings) -> AgentClient:
    """設定に応じてLLMエージェントクライアントを作成する。"""
    if settings.agent_runner_url.strip():
        from argos.services.agent.runner_client import RunnerAgentClient

        return RunnerAgentClient(settings)
    return RoutedAgentClient(settings)


def create_provider_client(settings: Settings, slot: AgentSlot) -> AgentClient:
    """1スロット分のproviderクライアントを作成する。"""
    provider = slot.provider.strip().lower()
    slot_settings = replace(settings, agent_provider=provider, agent_slots=(slot,))
    if provider == "codex":
        return CodexCliClient(slot_settings)
    if provider == "antigravity":
        return AntigravityCliClient(slot_settings)
    if provider == "hermes":
        return HermesCliClient(slot_settings)
    raise ValueError(f"未対応のエージェントプロバイダーです: {slot.provider}")
