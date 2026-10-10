from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.jsがない環境では実行しない")
def test_browser_audio_javascript() -> None:
    """ブラウザ音声のPCM解析と世代キャンセルをNode.js上で検証する。"""
    result = subprocess.run(
        ["node", "--test", "tests/js/test_browser_audio.js"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stdout + result.stderr
