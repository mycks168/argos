"""LLMエージェント連携。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

# client は各providerのCLIクライアントを取り込み、CLIクライアントは
# agent.session_store を参照する。__init__ で client を先に読み込むと循環importに
# なるため、公開シンボルは遅延解決する。
if TYPE_CHECKING:
    from argos.services.agent.client import AgentClient, create_agent_client, create_provider_client

__all__ = ["AgentClient", "create_agent_client", "create_provider_client"]


def __getattr__(name: str) -> Any:
    """公開シンボルを初回アクセス時に client から取り込む。"""
    if name in __all__:
        from argos.services.agent import client

        return getattr(client, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
