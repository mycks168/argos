import json

from argos_reminder.argos_client import ArgosClient
from argos_reminder.scheduler import create_reminder, parse_datetime


class FakeResponse:
    """urllibのレスポンスを模したテスト用オブジェクト。"""

    def __enter__(self):
        """with文に入る。"""
        return self

    def __exit__(self, exc_type, exc, tb):
        """with文から出る。"""
        return False

    def read(self):
        """JSONレスポンスを返す。"""
        return b'{"id":"notice"}'


def test_argos_client_sends_notification_payload(monkeypatch):
    """ARGOS通知APIへsound/speak付きpayloadを送る。"""
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    reminder = create_reminder(parse_datetime("2026-06-19 18:30"), "旅費申請", text="忘れずに")
    client = ArgosClient("http://argos.local:8765", "token")

    response = client.send_reminder(reminder)

    request, timeout = calls[0]
    payload = json.loads(request.data.decode("utf-8"))
    assert response == {"id": "notice"}
    assert request.full_url == "http://argos.local:8765/api/events"
    assert request.headers["Authorization"] == "Bearer token"
    assert timeout == 5
    assert payload["type"] == "notification"
    assert payload["title"] == "旅費申請"
    assert payload["text"] == "忘れずに"
    assert payload["sound"] is True
    assert payload["speak"] is True


def test_argos_client_gets_location(monkeypatch):
    """ARGOS現在地APIから緯度経度を取得する。"""
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        response = FakeResponse()
        response.read = lambda: b'{"lat":35.0,"lon":139.0}'
        return response

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = ArgosClient("http://argos.local:8765", "token")

    location = client.get_location()

    request, timeout = calls[0]
    assert location == (35.0, 139.0)
    assert request.full_url == "http://argos.local:8765/api/location"
    assert request.headers["Authorization"] == "Bearer token"
    assert timeout == 5


def test_argos_client_returns_none_when_location_is_unavailable(monkeypatch):
    """現在地APIが失敗した場合はNoneを返す。"""

    def fake_urlopen(_request, _timeout):
        raise OSError("gps unavailable")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = ArgosClient("http://argos.local:8765", "")

    assert client.get_location() is None
