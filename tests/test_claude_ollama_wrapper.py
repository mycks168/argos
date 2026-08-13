"""Claude Ollama用ラッパーの設定分離を確認する。"""

import os
import subprocess
from pathlib import Path


def test_wrapper_loads_private_environment_and_searxng_mcp(tmp_path: Path) -> None:
    """専用設定を読み、組み込み検索を無効化して実コマンドへ委譲する。"""
    config_dir = tmp_path / ".config" / "argos"
    config_dir.mkdir(parents=True)
    captured_args = tmp_path / "args.txt"
    captured_environment = tmp_path / "environment.txt"
    fake_claude = tmp_path / "fake-claude"
    fake_claude.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$@\" > '{captured_args}'\n"
        f"printf '%s' \"$ANTHROPIC_BASE_URL\" > '{captured_environment}'\n",
        encoding="utf-8",
    )
    fake_claude.chmod(0o700)
    mcp_config = config_dir / "searxng-mcp.json"
    mcp_config.write_text('{"mcpServers": {}}\n', encoding="utf-8")
    (config_dir / "claude-ollama.env").write_text(
        f"ANTHROPIC_BASE_URL=http://ollama.example:11434\nARGOS_CLAUDE_COMMAND={fake_claude}\n",
        encoding="utf-8",
    )
    wrapper = Path(__file__).parents[1] / "scripts" / "claude-ollama"

    subprocess.run(
        [str(wrapper), "-p", "確認"],
        check=True,
        env={**os.environ, "HOME": str(tmp_path)},
        timeout=10,
    )

    arguments = captured_args.read_text(encoding="utf-8").splitlines()
    assert arguments[:4] == ["--mcp-config", str(mcp_config), "--disallowedTools", "WebSearch"]
    assert arguments[-2:] == ["-p", "確認"]
    assert captured_environment.read_text(encoding="utf-8") == "http://ollama.example:11434"
