"""リモートArgosクライアントのテスト。"""

import json

from argos.config import AgentSlot, Settings
from argos.services.agent.remote_argos import RemoteArgosClient

from test_agent_client import _settings


class FakeResponse:
    """requestsレスポンスの最小スタブ。"""

    def __init__(self, lines=(), payload=None):
        """SSE行を保持する。"""
        self._lines = list(lines)
        self._payload = payload or {}
        self.closed = False

    def raise_for_status(self):
        """成功レスポンスとして扱う。"""

    def iter_lines(self, decode_unicode=False):
        """設定済みSSE行を返す。"""
        assert decode_unicode is True
        yield from self._lines

    def close(self):
        """クローズ済みを記録する。"""
        self.closed = True

    def json(self):
        """設定済みJSONを返す。"""
        return self._payload


def _remote_settings() -> Settings:
    """リモートスロットを含むテスト設定を返す。"""
    base = _settings()
    return Settings(
        **{
            **base.__dict__,
            "agent_slots": (
                AgentSlot(
                    name="自宅Codex",
                    provider="remote",
                    cwd="https://home.example",
                    slot_type="remote",
                    remote_url="https://home.example",
                    remote_token="secret",
                    remote_name="作業",
                    remote_provider="codex",
                    model="gpt-test",
                ),
            ),
        }
    )


def test_remote_argos_stream_selects_slot_and_returns_text(monkeypatch):
    """対象スロットを選択し、SSEのテキスト差分を返す。"""
    calls = []
    turn_response = FakeResponse(
        [
            'event: text',
            'data: ' + json.dumps({"event": "text", "delta": "こん"}),
            "",
            'data: ' + json.dumps({"event": "text", "delta": "にちは"}),
            'data: ' + json.dumps({"event": "done", "text": "こんにちは"}),
        ]
    )

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/api/terminal/turn"):
            return turn_response
        return FakeResponse()

    monkeypatch.setattr("argos.services.agent.remote_argos.requests.post", fake_post)
    client = RemoteArgosClient(_remote_settings(), _remote_settings().agent_slots[0])

    assert client.ask("確認") == "こんにちは"
    assert calls[0][0].endswith("/api/terminal/slots/select")
    assert calls[0][1]["json"] == {"name": "作業", "provider": "codex"}
    assert calls[1][1]["data"] == "確認".encode()
    assert calls[1][1]["headers"]["Authorization"] == "Bearer secret"
    assert turn_response.closed is True


def test_remote_argos_reset_selects_then_resets(monkeypatch):
    """リモート側の対象スロットだけをリセットする。"""
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr("argos.services.agent.remote_argos.requests.post", fake_post)
    client = RemoteArgosClient(_remote_settings(), _remote_settings().agent_slots[0])

    client.reset_current()

    assert calls[0][0].endswith("/api/terminal/slots/select")
    assert calls[1][0].endswith("/api/control")
    assert calls[1][1]["json"] == {"action": "reset_agent_session"}


def test_remote_argos_loads_history(monkeypatch):
    """リモート側の対象スロット履歴を取得する。"""
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        return FakeResponse(payload={"messages": [{"role": "assistant", "text": "前の回答"}]})

    monkeypatch.setattr("argos.services.agent.remote_argos.requests.get", fake_get)
    client = RemoteArgosClient(_remote_settings(), _remote_settings().agent_slots[0])

    assert client.load_current_history() == [{"role": "assistant", "text": "前の回答"}]
    assert "name=%E4%BD%9C%E6%A5%AD" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer secret"
