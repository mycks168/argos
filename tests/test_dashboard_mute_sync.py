"""ダッシュボード各画面の読み上げミュート同期を検証する。"""

from importlib.resources import files


def _static_html(filename: str) -> str:
    """パッケージに含まれるダッシュボードHTMLを読む。"""
    return files("argos.services.dashboard.static").joinpath(filename).read_text(encoding="utf-8")


def test_grid_uses_shared_dashboard_mute_state() -> None:
    """Gridは独自保存せずARGOS本体のミュート状態を操作する。"""
    grid_html = _static_html("grid.html")

    assert 'localStorage.setItem("argos-grid-muted"' not in grid_html
    assert "updateMuteState(Boolean(state.audio?.muted))" in grid_html
    assert 'body: JSON.stringify({action: isMuted ? "unmute" : "mute"})' in grid_html
    assert "if (nextMuted && !isMuted)" in grid_html


def test_dashboard_mute_stops_browser_playback() -> None:
    """通常・SP画面は共通ミュート状態でブラウザ音声も止める。"""
    dashboard_html = _static_html("dashboard.html")

    assert "if (muted) return;" in dashboard_html
    assert "function updateDashboardMuteState(nextMuted)" in dashboard_html
    assert "updateDashboardMuteState(Boolean(state.audio?.muted))" in dashboard_html
    assert "updateDashboardMuteState(result.muted)" in dashboard_html
