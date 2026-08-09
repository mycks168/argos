"""Webダッシュボードのキャッシュ付き進捗音声受信を検証する。"""

from importlib.resources import files


def _static_html(filename: str) -> str:
    """パッケージに含まれるダッシュボードHTMLを読む。"""
    return files("argos.services.dashboard.static").joinpath(filename).read_text(encoding="utf-8")


def test_dashboard_queues_progress_audio_url_with_browser_cache() -> None:
    """通常・SP画面はprogressイベントのURLを認証付きでキャッシュ再生する。"""
    dashboard_html = _static_html("dashboard.html")

    assert 'event.event === "progress" && event.url' in dashboard_html
    assert "queueVoiceAudioUrl(event.url, playbackGeneration)" in dashboard_html
    assert 'voicePlayer.enqueueUrl(audioUrl, {headers, cache: "force-cache"}' in dashboard_html


def test_grid_queues_progress_audio_for_target_tile() -> None:
    """Gridは対象タイルのフォーカス規則を保ったままprogress音声URLを再生する。"""
    grid_html = _static_html("grid.html")

    assert 'event.event === "progress" && event.url' in grid_html
    assert "queueAudioUrl(key, event.url, playbackGeneration)" in grid_html
    assert '{headers: authHeaders({}), cache: "force-cache"}' in grid_html
