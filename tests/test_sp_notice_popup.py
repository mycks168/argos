"""SP表示の通知欄の自動表示ロジックを、Node.js上で検証する。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.jsがない環境では実行しない")
def test_sp_notice_popup_javascript() -> None:
    """通知の対象判定、自動で開閉する流れ、利用者の操作を優先する動きを検証する。"""
    result = subprocess.run(
        ["node", "--test", "tests/js/test_sp_notice_popup.js"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stdout + result.stderr
