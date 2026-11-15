"""実際の起動スクリプトのURL生成だけを実行し、表示モードの優先順位を確認する。"""

import os
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest


@pytest.mark.parametrize("saved,fixed,configured,key,value", [
    ("sp", "", "", "startup_layout", "sp"),
    ("grid", "", "auto", "startup_layout", "grid"),
    ("sp", "standard", "", "layout", "standard"),
    ("sp", "", "standard", "layout", "standard"),
    ("sp", "grid", "standard", "layout", "grid"),
    ("bad", "", "", "", ""),
    ("", "", "", "", ""),
])
def test_kiosk_layout_url(tmp_path, saved, fixed, configured, key, value):
    """初回候補だけでは手動選択を強制上書きするURLを作らない。"""
    state = tmp_path / "argos"
    state.mkdir()
    if saved:
        (state / "dashboard-layout").write_text(saved + "\n")
    script = (Path(__file__).parents[1] / "scripts/open-dashboard-kiosk.sh").read_text()
    begin = script.index('DASHBOARD_URL="${DASHBOARD_SCHEME}')
    end = script.index("# 母艦起動直後", begin)
    env = {**os.environ, "XDG_STATE_HOME": str(tmp_path), "DASHBOARD_SCHEME": "http",
           "ARGOS_DASHBOARD_VIEW_KEY": "test-view", "ARGOS_DASHBOARD_PORT": "8765",
           "ARGOS_DASHBOARD_KIOSK_LAYOUT": fixed, "ARGOS_WINDOW_LAYOUT_DASHBOARD_LAYOUT": configured}
    result = subprocess.run(["bash", "-eu", "-c", script[begin:end] + '\nprintf "%s" "$DASHBOARD_URL"'],
                            env=env, capture_output=True, text=True, check=True)
    query = parse_qs(urlparse(result.stdout).query)
    assert query == {"key": ["test-view"], **({key: [value]} if key else {})}
