"""明示時だけ実ClaudeとChromiumで埋め込み端末を結合試験する。"""

from __future__ import annotations

import io
import os
import wave
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from argos.config import AgentSlot
from argos.core.app import ArgosApp, _TerminalGateway
from argos.services.agent.claude_tmux_client import ClaudeTmuxClient
from argos.services.agent.claude_tmux_session import run_tmux
from argos.services.dashboard.server import DashboardServer
from test_app import _patch_app, _settings

if TYPE_CHECKING:
    from playwright.sync_api import Page, Route


def _verify_layouts(page: Page, base_url: str) -> None:
    """全レイアウトで端末入力が回答・音声へ一度だけ流れる。"""
    for route in ["/", "/sp", "/grid"]:
        page.goto(base_url + route)
        frame = page.frame_locator("iframe.argos-console-frame")
        frame.locator("#status").filter(has_text="接続済み").wait_for(timeout=30000)
        bounds = frame.locator(".xterm-screen").bounding_box()
        assert bounds is not None and bounds["height"] > 100
        frame.locator(".xterm-helper-textarea").focus()
        page.keyboard.type("Reply with exactly BROWSER_OK and nothing else.")
        with page.expect_response(lambda response: "input_mode=observe" in response.url, timeout=15000) as observed:
            page.keyboard.press("Enter")
        body = observed.value.text()
        assert '"event": "done"' in body and "BROWSER_OK" in body
        assert '"event": "audio"' in body


def _verify_microphone(page: Page) -> None:
    """ブラウザの録音開始・終了からSTT・実Claude・応答WAVまで接続する。"""
    frame = page.frame_locator("iframe.argos-console-frame")
    frame.locator("#voice").click()
    page.wait_for_timeout(700)
    with page.expect_response(lambda response: "/api/terminal/turn?" in response.url and "observe" not in response.url, timeout=15000) as response:
        frame.locator("#voice").click()
    body = response.value.text()
    assert '"event": "transcript"' in body and "こんにちは" in body
    assert '"event": "done"' in body and '"event": "audio"' in body


def _verify_recording_reentry(page: Page, base_url: str) -> None:
    """通常・SPで録音状態を解除し、同じ画面で次の録音も送れる。"""
    for route in ["/", "/sp"]:
        page.goto(base_url + route)
        page.frame_locator("iframe.argos-console-frame").locator("#status").filter(has_text="接続済み").wait_for(timeout=30000)
        for _attempt in range(2):
            _verify_microphone(page)
            page.wait_for_function("voiceState !== 'recording' && voiceRecorder === null")


def _verify_lost_submit_notification(page: Page, client: ClaudeTmuxClient) -> None:
    """回収要求の切断後に次の入力が来ても、再接続で最終回答を回収する。"""
    should_block = True

    def intercept_observation(route: Route) -> None:
        """音声API以外は通し、回答回収だけを一時的に切断する。"""
        if should_block and "input_mode=observe" in route.request.url:
            route.abort()
        else:
            route.continue_()

    pattern = "**/api/terminal/turn?*"
    page.route(pattern, intercept_observation)
    frame = page.frame_locator("iframe.argos-console-frame")
    offset = client._session.journal_path.stat().st_size
    frame.locator(".xterm-helper-textarea").focus()
    page.keyboard.type("Reply with exactly RECOVERY_OK and nothing else.")
    page.keyboard.press("Enter")
    page.wait_for_timeout(1000)
    assert "RECOVERY_OK" in client._wait_answer(client.terminal_target(), offset)
    page.keyboard.type("draft")
    page.keyboard.press("Control+u")
    page.wait_for_timeout(500)
    assert client.has_pending_manual_turn()
    should_block = False
    with page.expect_response(lambda response: "input_mode=observe" in response.url, timeout=20000) as response:
        frame.locator("#reconnect").click()
    assert "RECOVERY_OK" in response.value.text()
    page.unroute(pattern, intercept_observation)


def _verify_ptt(page: Page) -> None:
    """端末にフォーカスがあってもPTTの押下・解放が親画面へ届く。"""
    frame = page.frame_locator("iframe.argos-console-frame")
    frame.locator(".xterm-helper-textarea").focus()
    frame.locator(".xterm-helper-textarea").dispatch_event("keydown", {"key":"F13", "code":"F13", "keyCode":124, "bubbles":True})
    page.wait_for_timeout(700)
    with page.expect_response(lambda response: "/api/terminal/turn?" in response.url and "observe" not in response.url, timeout=15000) as response:
        frame.locator(".xterm-helper-textarea").dispatch_event("keyup", {"key":"F13", "code":"F13", "keyCode":124, "bubbles":True})
    assert '"event": "done"' in response.value.text()


def _silent_wav() -> bytes:
    """再生経路の検証ではスピーカーへ音を出さない。"""
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 1600)
    return output.getvalue()


@pytest.mark.skipif(os.environ.get("ARGOS_TEST_CONSOLE_BROWSER") != "1", reason="実Claudeとブラウザは明示時だけ実行する")
@pytest.mark.parametrize("provider", ["claude", "codex", "antigravity"])
def test_browser_console(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str) -> None:
    """通常/SP/Grid、サイズ追従、再接続、端末回答の音声化を実測する。"""
    from playwright.sync_api import sync_playwright

    _patch_app(monkeypatch)
    extra_args = ("--tools=", "--strict-mcp-config", "--setting-sources=") if provider == "claude" else ()
    slot = AgentSlot("ブラウザ試験", provider, "/opt/argos", execution_mode="tmux", tmux_timeout_seconds=120, extra_args=extra_args)
    settings = replace(_settings(), agent_slots=(slot,), agent_state_path=str(tmp_path / "sessions.json"), claude_model="")
    app = ArgosApp(settings)
    client = ClaudeTmuxClient(settings, slot)
    app._agent = client
    app._dashboard_state.set_agent(slot.name, slot.provider)
    app._dashboard_state.set_slots([(slot.name, slot.provider)])
    audio = _silent_wav()
    monkeypatch.setattr(app._speech, "synthesize_response_stream", lambda deltas, _key: (
        event for delta in deltas for event in [("text", delta), ("audio", audio)]))
    gateway = _TerminalGateway(app)
    server = DashboardServer(app._dashboard_state, "127.0.0.1", 0, "browser-test-token", terminal_handler=gateway)
    server.start()
    base_url = f"http://127.0.0.1:{server.address[1]}"
    errors: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path="/snap/bin/chromium", headless=True,
                args=["--no-sandbox", "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            _verify_layouts(page, base_url)
            _verify_lost_submit_notification(page, client)
            _verify_microphone(page)
            _verify_ptt(page)
            if provider == "claude":
                _verify_recording_reentry(page, base_url)
            state = client.terminal_target()
            before = run_tmux(["display-message", "-p", "-t", state.pane_id, "#{pane_width}"])
            page.set_viewport_size({"width": 1000, "height": 800})
            page.wait_for_timeout(1500)
            after = run_tmux(["display-message", "-p", "-t", state.pane_id, "#{pane_width}"])
            assert before != after
            assert not (client._session.directory / "manual.json").exists()
            # 再読込してもCLIを作り直さず、前の出力が端末に残る。
            page.reload()
            page.frame_locator("iframe.argos-console-frame").locator("#status").filter(has_text="接続済み").wait_for(timeout=30000)
            assert client.terminal_target().session_id == state.session_id
            browser.close()
        assert errors == []
    finally:
        server.stop()
        if client.terminal_session_name:
            run_tmux(["kill-session", "-t", f"={client.terminal_session_name}"])
