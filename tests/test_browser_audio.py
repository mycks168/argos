from __future__ import annotations

import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
