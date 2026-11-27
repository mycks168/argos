"""待機画面設定の配信とブラウザ側の動作を検証する。"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from urllib.request import urlopen

import pytest

from argos.services.dashboard.server import DashboardServer
from argos.services.dashboard.state import DashboardState
from argos.yaml_config import load_yaml_environment


def test_idle_url_from_yaml(tmp_path: Path) -> None:
    """共通URLは通常の階層設定から読み込める。"""
    path = tmp_path / "config.yaml"
    path.write_text('dashboard:\n  idle_url: https://display.test/\n', encoding="utf-8")
    assert load_yaml_environment(path)["ARGOS_DASHBOARD_IDLE_URL"] == "https://display.test/"


@pytest.mark.parametrize("page", ["/", "/sp", "/grid", "/settings"])
def test_idle_config_delivery(page: str) -> None:
    """全レイアウトに共通設定を渡し、URLからscriptタグを注入できない。"""
    url = 'https://display.test/?q=</script><script>alert(1)</script>'
    server = DashboardServer(DashboardState(), "127.0.0.1", 0, "", screensaver_seconds=12.5, idle_url=url)
    server.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.address[1]}{page}", timeout=2) as response:
            html = response.read().decode()
        expected = json.dumps({"url": url, "timeoutMs": 12500.0}, ensure_ascii=True).replace("<", "\\u003c")
        assert expected in html
        assert "__ARGOS_IDLE_CONFIG__" not in html
        assert url not in html
        assert '/static/idle_screen.js' in html
    finally:
        server.stop()


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.jsがない環境では実行しない")
@pytest.mark.parametrize("script", ["test_idle_screen.js", "test_grid_state_events.js"])
def test_idle_javascript(script: str) -> None:
    """無操作・復帰・外部ページの失敗・端末別保存を検証する。"""
    result = subprocess.run(
        ["node", "--test", f"tests/js/{script}"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, check=False, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
