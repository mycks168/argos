"""Webダッシュボードのキャッシュ付き進捗音声受信を検証する。"""

from importlib.resources import files


def _static_html(filename: str) -> str:
    """パッケージに含まれるダッシュボードHTMLを読む。"""
    return files("argos.services.dashboard.static").joinpath(filename).read_text(encoding="utf-8")


def test_dashboard_queues_progress_audio_url_with_browser_cache() -> None:
    """通常・SP画面はprogressイベントのURLを認証付きでキャッシュ再生する。"""
    dashboard_html = _static_html("dashboard.html")

    assert 'event.event === "progress" && event.url' in dashboard_html
    assert "queueVoiceAudioUrl(event.url, playbackGeneration, slotKey)" in dashboard_html
    assert 'slotAudio.enqueue(slotKey, {url:audioUrl, options:{headers, cache:"force-cache"}}' in dashboard_html


def test_grid_queues_progress_audio_for_target_tile() -> None:
    """Gridは対象タイルのフォーカス規則を保ったままprogress音声URLを再生する。"""
    grid_html = _static_html("grid.html")

    assert 'event.event === "progress" && event.url' in grid_html
    assert "queueAudioUrl(key, event.url, playbackGeneration)" in grid_html
    assert 'options:{headers:authHeaders({}), cache:"force-cache"}' in grid_html


def test_dashboards_render_source_urls_as_safe_links() -> None:
    """通常画面とGridは共通処理で回答本文（Markdown、図、出典URLのリンク）を描画する。"""
    dashboard_html = _static_html("dashboard.html")
    grid_html = _static_html("grid.html")

    for html in (dashboard_html, grid_html):
        assert '<link rel="stylesheet" href="/static/message_view.css">' in html
        # 本文の変換が同梱のmarkdown-itを使うため、先に読み込む。
        assert html.index('<script src="/static/vendor/markdown-it.min.js"></script>') < html.index(
            '<script src="/static/message_text.js"></script>'
        )
        assert html.index('<script src="/static/message_text.js"></script>') < html.index(
            '<script src="/static/message_view.js"></script>'
        )
        # mermaidは大きいので、ページでは読み込まず、図が出たときに表示処理が読み込む。
        assert "mermaid.min.js" not in html
    assert "ArgosMessageView.sync(messages, visibleMessages, {" in dashboard_html
    assert "ArgosMessageView.sync(container, messages.map(" in grid_html
