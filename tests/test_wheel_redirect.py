"""会話欄へのホイール付け替えを、Node.js上で検証する。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.jsがない環境では実行しない")
def test_wheel_redirect_javascript() -> None:
    """どこで回しても会話欄をスクロールし、自分でスクロールする場所は邪魔しないことを検証する。"""
    result = subprocess.run(
        ["node", "--test", "tests/js/test_wheel_redirect.js"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_dashboard_loads_wheel_redirect() -> None:
    """ダッシュボードが付け替え処理を読み込み、会話欄へ登録している。"""
    html = (PROJECT_ROOT / "src/argos/services/dashboard/static/dashboard.html").read_text(encoding="utf-8")
    assert '<script src="/static/wheel_redirect.js"></script>' in html
    assert "window.ArgosWheelRedirect.install(window, messages);" in html
