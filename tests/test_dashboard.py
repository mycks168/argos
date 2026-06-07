import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from argos.services.dashboard.server import DashboardServer, _apply_event
from argos.services.dashboard.state import DashboardState


def _read_json(url, method="GET", payload=None, token=""):
    """HTTP APIを呼び出してJSONレスポンスを返す。"""
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=body, headers=headers, method=method)
    with urlopen(request, timeout=2) as response:
        return response.status, json.loads(response.read())


def test_dashboard_state_keeps_messages_notifications_and_status():
    """画面へ表示する状態をまとめて保持できる。"""
    state = DashboardState()
    state.set_status("thinking", "考え中")
    state.set_agent("アンチグラビティ", "antigravity")
    state.set_audio_muted(True)
    message_id = state.add_message("assistant", "", streaming=True)
    state.append_message(message_id, "返答")
    state.finish_message(message_id)
    state.add_notification("メール", "新着があります", source="mail")

    snapshot = state.snapshot()

    assert snapshot["status"]["code"] == "thinking"
    assert snapshot["agent"]["name"] == "アンチグラビティ"
    assert snapshot["agent"]["provider"] == "antigravity"
    assert snapshot["audio"]["muted"] is True
    assert snapshot["messages"][0]["text"] == "返答"
    assert snapshot["messages"][0]["streaming"] is False
    assert snapshot["notifications"][0]["title"] == "メール"


def test_dashboard_state_notifies_subscribers():
    """状態更新をSSE用キューへ通知する。"""
    state = DashboardState()
    subscriber = state.subscribe()

    state.add_message("user", "こんにちは")

    assert subscriber.get(timeout=1) > 0
    state.unsubscribe(subscriber)


def test_dashboard_state_deduplicates_consecutive_internal_errors():
    """同一の内部エラーが連続しても通知を増やさない。"""
    state = DashboardState()

    first_id = state.add_error_notification("VOICEVOX", "接続できません")
    second_id = state.add_error_notification("VOICEVOX", "接続できません")

    snapshot = state.snapshot()
    assert first_id == second_id
    assert len(snapshot["notifications"]) == 1
    assert snapshot["notifications"][0]["priority"] == "high"


def test_apply_event_supports_messages_status_and_clear():
    """外部APIから会話、状態、通知削除を更新できる。"""
    state = DashboardState()

    _apply_event(state, {"type": "user_message", "text": "ユーザー発話"})
    _apply_event(state, {"type": "agent_message", "text": "ARGOS返答"})
    _apply_event(state, {"type": "status", "code": "thinking", "label": "考え中"})
    state.add_notification("通知", "本文")
    _apply_event(state, {"type": "clear_notifications"})

    snapshot = state.snapshot()
    assert [message["role"] for message in snapshot["messages"]] == ["user", "assistant"]
    assert snapshot["status"]["code"] == "thinking"
    assert snapshot["notifications"] == []


def test_apply_event_rejects_unknown_type():
    """未対応イベントはエラーにする。"""
    with pytest.raises(ValueError, match="未対応"):
        _apply_event(DashboardState(), {"type": "unknown"})


def test_dashboard_server_serves_html_snapshot_and_authenticated_events(tmp_path):
    """画面配信とBearer認証付きイベントAPIを利用できる。"""
    state = DashboardState()
    snapshot_path = tmp_path / "camera-latest.jpg"
    snapshot_path.write_bytes(b"jpeg-data")
    server = DashboardServer(state, "127.0.0.1", 0, "secret", snapshot_path)
    server.start()
    base_url = f"http://{server.address[0]}:{server.address[1]}"
    try:
        with urlopen(base_url + "/", timeout=2) as response:
            html = response.read().decode("utf-8")
        assert "ARGOS Dashboard" in html
        assert "cursor: none" in html
        assert "cursor: none !important" in html
        assert "nextNotifications !== previousNotifications" in html
        assert "touch-action: pan-y" in html
        assert "followLatestMessage" in html
        assert "const visibleMessages = state.messages;" in html
        assert "state.notifications.slice().reverse()" in html
        assert "state.notifications.slice(-4)" not in html
        assert ".notifications::-webkit-scrollbar" in html
        assert "id=\"splash\"" in html
        assert "showSplash()" in html
        assert 'data-code="booting"' in html
        assert 'stream.addEventListener("open", refresh)' in html
        assert 'data-code="locked"' in html
        assert 'data-code="alert"' in html
        assert "CURRENT SLOT" in html
        assert 'id="agent-name"' in html
        assert "state.agent?.provider" in html
        assert 'id="mute-button"' in html
        assert "const dashboardToken = \"secret\";" in html
        assert 'data-code="muted"' in html

        with urlopen(base_url + "/camera/latest.jpg", timeout=2) as response:
            assert response.headers["Content-Type"] == "image/jpeg"
            assert response.read() == b"jpeg-data"

        status, body = _read_json(base_url + "/api/events", "POST", {"type": "notification", "title": "テスト"}, "secret")
        assert status == 201
        assert body["id"]

        status, body = _read_json(base_url + "/api/state")
        assert status == 200
        assert body["notifications"][0]["title"] == "テスト"
    finally:
        server.stop()


def test_dashboard_control_api_calls_handler():
    """ダッシュボード操作APIはBearer認証後にハンドラーを呼ぶ。"""
    calls = []

    def handle_control(action):
        calls.append(action)
        return {"muted": action == "mute"}

    server = DashboardServer(DashboardState(), "127.0.0.1", 0, "secret", control_handler=handle_control)
    server.start()
    url = f"http://{server.address[0]}:{server.address[1]}/api/control"
    try:
        status, body = _read_json(url, "POST", {"action": "mute"}, "secret")
    finally:
        server.stop()

    assert status == 200
    assert body == {"muted": True}
    assert calls == ["mute"]


def test_dashboard_server_rejects_invalid_token():
    """外部イベントAPIはBearer認証なしで更新できない。"""
    server = DashboardServer(DashboardState(), "127.0.0.1", 0, "secret")
    server.start()
    url = f"http://{server.address[0]}:{server.address[1]}/api/events"
    try:
        try:
            _read_json(url, "POST", {"type": "notification", "title": "テスト"})
        except HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("認証エラーになりませんでした")
    finally:
        server.stop()
