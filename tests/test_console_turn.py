"""送信済み端末入力が二重送信されず既存の音声経路を通ることを確認する。"""

from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

from argos.core.app import ArgosApp, _TerminalGateway
from argos.services.agent.console_protocol import CloseConsole, ConnectionId, ObservedTerminalInput
from argos.services.agent.remote_argos import RemoteArgosClient
from test_app import _patch_app, _settings
from test_terminal_api import FakeTerminalHandler, _request, _start_server
from test_remote_argos import FakeResponse, _remote_settings


def test_observed_app_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """履歴・完了状態・音声チャンクは従来のターン処理で作る。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    ask = Mock()
    monkeypatch.setattr(app._agent, "ask_stream", ask)
    events = list(app._terminal_process_turn(text=ObservedTerminalInput("端末から入力", ["端末の回答"])))
    ask.assert_not_called()
    assert any(event["event"] == "text" for event in events)
    assert events[-1] == {"event": "done", "text": "端末の回答"}
    assert not app._dashboard_state.snapshot()["agent"].get("busy", False)


def test_gateway_observation_and_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """直接端末は本人確認ロックを迂回しない。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    gateway = _TerminalGateway(app)
    gateway._console = Mock()
    gateway._console.observe.return_value = iter(["回答"])
    gateway.console_request(CloseConsole(ConnectionId("test")))
    gateway._console.execute.assert_called_once()
    assert list(gateway.observe_turn("入力済み", False, "", ""))[-1]["text"] == "回答"
    gateway._console.observe.assert_called_once_with("作業", "codex")
    monkeypatch.setattr(app._auth_coord, "is_locked", lambda: True)
    with pytest.raises(RuntimeError, match="本人確認"):
        gateway.console_request(CloseConsole(ConnectionId("test")))
    gateway.close_console()
    gateway._console.close.assert_called_once()


def test_observe_http_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """観測要求のスロットと音声設定がHTTPから欠落せず渡る。"""
    handler = FakeTerminalHandler([])
    observe = Mock(return_value=iter([{"event": "done", "text": "回答"}]))
    monkeypatch.setattr(handler, "observe_turn", observe, raising=False)
    server, base = _start_server(handler)
    try:
        with _request(base + "/api/terminal/turn?input_mode=observe&name=test&provider=claude", method="POST", body=b"sent", content_type="text/plain") as response:
            assert "回答" in response.read().decode()
        observe.assert_called_once_with("sent", True, "test", "claude")
        assert handler.received_text is None
    finally:
        server.stop()


def test_observe_http_no_fallback() -> None:
    """未対応先で観測要求を通常のプロンプトとして誤送信しない。"""
    handler = FakeTerminalHandler([])
    server, base = _start_server(handler)
    try:
        for query, status in [("observe", 503), ("unknown", 400)]:
            with pytest.raises(HTTPError) as error:
                _request(base + f"/api/terminal/turn?input_mode={query}", method="POST", body=b"sent", content_type="text/plain")
            assert error.value.code == status
        assert handler.received_text is None
    finally:
        server.stop()


def test_remote_observe(monkeypatch: pytest.MonkeyPatch) -> None:
    """リモート観測は対象スロットを明示し、接続先では音声化しない。"""
    response = FakeResponse(['data: {"event":"text","delta":"answer"}'])
    post = Mock(return_value=response)
    monkeypatch.setattr("argos.services.agent.remote_argos.requests.post", post)
    settings = _remote_settings()
    client = RemoteArgosClient(settings, settings.agent_slots[0])
    assert list(client.observe_stream()) == ["answer"]
    assert "input_mode=observe" in post.call_args.args[0]
    assert post.call_args.kwargs["headers"]["Accept"] == "text/event-stream"
    assert response.closed
