"""Agent Runner のエントリーポイント。"""

from __future__ import annotations

import logging
from pathlib import Path

from argos.config import load_settings
from argos.services.agent.runner import AgentJobStore, AgentRunner, AgentRunnerServer


def main() -> None:
    """設定を読み込み、Agent Runner HTTPサーバーを起動する。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    store = AgentJobStore(Path(settings.agent_runner_state_dir))
    runner = AgentRunner(settings, store)
    server = AgentRunnerServer(runner, token=settings.agent_runner_token)
    server.serve_forever(settings.agent_runner_host, settings.agent_runner_port)


if __name__ == "__main__":
    main()
