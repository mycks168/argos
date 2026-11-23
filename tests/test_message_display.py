"""会話本文のリッチ表示（Markdown・図・表・画像）を、Node.js上で検証する。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "src/argos/services/dashboard/static"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.jsがない環境では実行しない")
@pytest.mark.parametrize(
    "script",
    ["tests/js/test_message_text.js", "tests/js/test_message_view.js", "tests/js/test_message_time_labels.js"],
)
def test_message_display_javascript(script: str) -> None:
    """本文のHTML化と、会話欄の差分更新・図の描画・拡大表示を検証する。"""
    result = subprocess.run(
        ["node", "--test", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_vendor_libraries_are_bundled_with_licenses() -> None:
    """オフラインでも表示できるよう、ライブラリとライセンスを同梱している。"""
    for name in ("markdown-it.min.js", "markdown-it.LICENSE", "mermaid.min.js", "mermaid.LICENSE"):
        assert (STATIC_DIR / "vendor" / name).stat().st_size > 0


def test_message_styles_do_not_assume_screen_size() -> None:
    """440px高の車載画面から4Kまで使うため、見た目は画面の大きさを決め打ちしない。"""
    css = (STATIC_DIR / "message_view.css").read_text(encoding="utf-8")

    assert "@media" not in css
    assert "max-width: 100%" in css
    assert "max-height: min(70vh, 40em)" in css
