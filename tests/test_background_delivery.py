"""子の回答配信が親の状態や通常入力を変更しないことを検証する。"""

from contextlib import nullcontext
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

from argos.services.agent.remote_argos import RemoteArgosClient
from test_remote_argos import FakeResponse, _remote_settings
from argos.core.app import ArgosApp, _TerminalGateway
from test_app import _patch_app, _settings
from test_terminal_api import FakeTerminalHandler, _request, _start_server


def test_background_does_not_block_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    """親が実行中でも子の回答を履歴と音声へ渡し、入力状態は変更しない。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    gateway = _TerminalGateway(app)
    gateway._console = Mock()
    gateway._console.background.return_value = nullcontext("子の結果")
    app._dashboard_state.set_slot_busy("作業", "codex", True)
    before = app._dashboard_state.snapshot()["status"]
    monkeypatch.setattr(
        app._speech,
        "synthesize_response_stream",
        lambda _deltas, _key: [("text", "子の結果"), ("audio", b"wav")],
    )
    events = list(gateway.background_turn("a" * 64, True, "作業", "codex"))
    assert events[-1]["event"] == "done"
    assert any(event["event"] == "audio" for event in events)
    assert app._dashboard_state.snapshot()["status"] == before
    assert app._dashboard_state.snapshot()["slots"][0]["busy"]
    list(gateway.background_turn("a" * 64, False, "作業", "codex"))
    assert len(app._dashboard_state.slot_messages("作業", "codex")) == 1
    gateway._console.observe.assert_not_called()
    gateway._console.background.return_value = nullcontext("子エージェントの回答です。\n遠隔の結果")
    events = list(gateway.background_turn("b" * 64, False, "作業", "codex"))
    assert str(events[-1]["text"]).count("子エージェントの回答です。") == 1


def test_background_auth_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """本人確認と取得失敗を通常入力へフォールバックさせない。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    gateway = _TerminalGateway(app)
    gateway._console = Mock()
    monkeypatch.setattr(app._auth_coord, "is_locked", lambda: True)
    assert (
        list(gateway.background_turn("a" * 64, True, "作業", "codex"))[0]["event"]
        == "error"
    )
    gateway._console.background.assert_not_called()
    monkeypatch.setattr(app._auth_coord, "is_locked", lambda: False)
    gateway._console.background.side_effect = ValueError("不正な識別子")
    assert (
        list(gateway.background_turn("bad", True, "作業", "codex"))[0]["event"]
        == "error"
    )


def test_background_http_and_old_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    """既存の認証付きturn経路を使い、旧版では安全に拒否する。"""
    handler = FakeTerminalHandler([])
    server, base = _start_server(handler)
    try:
        url = (
            base + "/api/terminal/turn?input_mode=background&name=test&provider=claude"
        )
        with pytest.raises(HTTPError) as error:
            _request(url, method="POST", body=b"event", content_type="text/plain")
        assert error.value.code == 503
        background = Mock(return_value=iter([{"event": "done", "text": "子の結果"}]))
        monkeypatch.setattr(handler, "background_turn", background, raising=False)
        with _request(
            url, method="POST", body=b"event", content_type="text/plain"
        ) as response:
            assert "子の結果" in response.read().decode()
        background.assert_called_once_with("event", True, "test", "claude")
        assert handler.received_text is None
    finally:
        server.stop()


def test_remote_background_does_not_select_or_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """遠隔スロットを切り替えず、認証付きの子回収要求だけを送る。"""
    settings = _remote_settings()
    client = RemoteArgosClient(settings, settings.agent_slots[0])
    response = FakeResponse(
        ['data: {"event":"text","delta":"子の結果"}', 'data: {"event":"done"}']
    )
    post = Mock(return_value=response)
    monkeypatch.setattr("argos.services.agent.remote_argos.requests.post", post)
    assert list(client.background_stream("a" * 64)) == ["子の結果"]
    post.assert_called_once()
    assert "input_mode=background" in post.call_args.args[0]
    assert post.call_args.kwargs["data"] == b"a" * 64
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer secret"
    assert response.closed
    response._lines = []
    with pytest.raises(RuntimeError, match="完了前"):
        list(client.background_stream("a" * 64))
