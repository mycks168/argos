"""実ブラウザで3画面の子回答経路を確認し、実音声やCLIは起動しない。"""

import os
from unittest.mock import Mock

import pytest

from test_terminal_api import FakeTerminalHandler, _start_server


@pytest.mark.skipif(
    os.environ.get("ARGOS_TEST_BACKGROUND_BROWSER") != "1",
    reason="Chromiumは明示時だけ起動する",
)
def test_background_in_all_layouts() -> None:
    """録音中に届いた回答を全画面で保留し、録音後だけ順番に再生する。"""
    from playwright.sync_api import sync_playwright

    handler = FakeTerminalHandler([])
    handler.background_turn = Mock(
        side_effect=lambda *_arguments: iter(
            [
                {"event": "audio", "data": "part1"},
                {"event": "audio", "data": "part2"},
                {"event": "done"},
            ]
        )
    )
    server, base_url = _start_server(handler)
    errors: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path="/snap/bin/chromium",
                headless=True,
                args=["--no-sandbox"],
            )
            page = browser.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            for route in ["/", "/sp", "/grid"]:
                page.goto(base_url + route)
                page.wait_for_function("typeof backgroundReceiver !== 'undefined'")
                page.evaluate("""() => {
                    if (typeof muted !== 'undefined') muted = false;
                    if (typeof isMuted !== 'undefined') isMuted = false;
                    window.played = [];
                    slotAudio.player = {generation:0, cancel() {}, enqueueBase64(data) {window.played.push(data); return Promise.resolve();}};
                    slotAudio.select('codex\\u0000作業');
                    slotAudio.pause();
                    backgroundReceiver.receive({name:'作業', provider:'codex'}, ['a'.repeat(64)]);
                }""")
                page.wait_for_function("backgroundReceiver.completed.size === 1")
                assert page.evaluate("window.played") == []
                page.evaluate("slotAudio.resume()")
                page.wait_for_function("window.played.length === 2")
                assert page.evaluate("window.played") == ["part1", "part2"]
            browser.close()
        assert errors == []
        assert handler.background_turn.call_count == 3
        assert handler.received_text is None
    finally:
        server.stop()
