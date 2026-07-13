"""PiZero端末API（Terminal API）と関連処理のテスト。"""

import base64
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from argos.config import Settings
from argos.core.app import ArgosApp, _TerminalGateway
from argos.services.dashboard.server import DashboardServer
from argos.services.dashboard.state import DashboardState

from test_app import _patch_app, _settings


def _read_sse_events(response):
    """SSEレスポンスをイベント名とJSONデータのリストへ変換する。"""
    events = []
    event_name = "message"
    for raw in response:
        line = raw.decode("utf-8").rstrip("\n")
        if line.startswith("event: "):
            event_name = line[len("event: ") :]
        elif line.startswith("data: "):
            events.append((event_name, json.loads(line[len("data: ") :])))
            event_name = "message"
    return events


class FakeTerminalHandler:
    """DashboardServerへ渡す端末ハンドラのテスト用スタブ。"""

    def __init__(self, turn_events):
        """process_turnで返すイベント列を保持する。"""
        self._turn_events = turn_events
        self.received_wav = None

    def list_slots(self):
        return {"slots": [{"name": "作業", "provider": "codex", "active": True}], "current": {"name": "作業", "provider": "codex"}}

    def next_slot(self):
        return {"slots": [{"name": "次", "provider": "antigravity", "active": True}], "current": {"name": "次", "provider": "antigravity"}}

    def process_turn(self, wav_bytes):
        self.received_wav = wav_bytes
        yield from self._turn_events


def _start_server(handler, token="secret"):
    """端末ハンドラ付きのDashboardServerを起動しベースURLを返す。"""
    server = DashboardServer(DashboardState(), "127.0.0.1", 0, token, terminal_handler=handler)
    server.start()
    base_url = f"http://{server.address[0]}:{server.address[1]}"
    return server, base_url


def _request(url, method="GET", body=None, token="secret", content_type="application/json"):
    """Terminal APIへHTTPリクエストを送りレスポンスを返す。"""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None and content_type:
        headers["Content-Type"] = content_type
    return urlopen(Request(url, data=body, headers=headers, method=method), timeout=3)


# --- DashboardServer 端末エンドポイント ---


def test_terminal_slots_requires_token():
    """スロット一覧APIはBearer認証を必須にする。"""
    handler = FakeTerminalHandler([])
    server, base_url = _start_server(handler)
    try:
        with pytest.raises(HTTPError) as exc:
            _request(base_url + "/api/terminal/slots", token="")
        assert exc.value.code == 401
    finally:
        server.stop()


def test_terminal_slots_returns_current():
    """スロット一覧APIは現在スロットを返す。"""
    handler = FakeTerminalHandler([])
    server, base_url = _start_server(handler)
    try:
        with _request(base_url + "/api/terminal/slots") as response:
            payload = json.loads(response.read())
        assert payload["current"]["name"] == "作業"
        assert payload["slots"][0]["active"] is True
    finally:
        server.stop()


def test_terminal_next_slot_cycles():
    """スロット巡回APIは切替後のスロットを返す。"""
    handler = FakeTerminalHandler([])
    server, base_url = _start_server(handler)
    try:
        with _request(base_url + "/api/terminal/slots/next", method="POST", body=b"") as response:
            payload = json.loads(response.read())
        assert payload["current"]["name"] == "次"
    finally:
        server.stop()


def test_terminal_turn_streams_sse_events():
    """ターンAPIはWAVを受け取りSSEでイベントを返す。"""
    turn_events = [
        {"event": "transcript", "text": "こんにちは"},
        {"event": "text", "delta": "応答"},
        {"event": "audio", "seq": 0, "format": "wav", "data": base64.b64encode(b"WAV").decode("ascii")},
        {"event": "done", "text": "応答"},
    ]
    handler = FakeTerminalHandler(turn_events)
    server, base_url = _start_server(handler)
    try:
        with _request(base_url + "/api/terminal/turn", method="POST", body=b"RIFFDATA", content_type="audio/wav") as response:
            events = _read_sse_events(response)
        assert handler.received_wav == b"RIFFDATA"
        names = [name for name, _ in events]
        assert names == ["transcript", "text", "audio", "done"]
        assert events[0][1]["text"] == "こんにちは"
        assert base64.b64decode(events[2][1]["data"]) == b"WAV"
    finally:
        server.stop()


def test_terminal_turn_rejects_empty_body():
    """ターンAPIは空ボディを拒否する。"""
    handler = FakeTerminalHandler([])
    server, base_url = _start_server(handler)
    try:
        with pytest.raises(HTTPError) as exc:
            _request(base_url + "/api/terminal/turn", method="POST", body=b"", content_type="audio/wav")
        assert exc.value.code == 400
    finally:
        server.stop()


def test_terminal_endpoints_disabled_without_handler():
    """端末ハンドラ未設定なら503を返す。"""
    server = DashboardServer(DashboardState(), "127.0.0.1", 0, "secret")
    server.start()
    base_url = f"http://{server.address[0]}:{server.address[1]}"
    try:
        with pytest.raises(HTTPError) as exc:
            _request(base_url + "/api/terminal/slots")
        assert exc.value.code == 503
    finally:
        server.stop()


# --- ArgosApp 端末ターン処理 ---


def test_app_terminal_process_turn_streams_and_records(monkeypatch):
    """端末ターンがSTT→応答→合成の順にイベントを生成し、ダッシュボードへ記録する。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    # 合成部はテキストと音声の両方を返すスタブに差し替える。
    monkeypatch.setattr(
        app._speech,
        "synthesize_response_stream",
        lambda deltas, slot_key="": iter([("text", "".join(deltas)), ("audio", b"WAV")]),
    )
    events = list(app._terminal_process_turn(b"RIFFDATA"))
    names = [e["event"] for e in events]
    assert names == ["transcript", "text", "audio", "done"]
    assert events[0]["text"] == "こんにちは"  # FakeStt
    assert events[1]["delta"] == "応答"  # FakeCodex.ask_stream の応答差分
    assert base64.b64decode(events[2]["data"]) == b"WAV"
    assert events[2]["seq"] == 0
    # ダッシュボードにユーザ発話と応答が記録される。
    messages = app._dashboard_state.snapshot()["messages"]
    roles = [(m["role"], m["text"]) for m in messages]
    assert ("user", "こんにちは") in roles
    assert ("assistant", "応答") in roles


def test_app_terminal_process_turn_updates_dashboard_status(monkeypatch):
    """端末ターン中に母艦の状態枠が遷移し、完了で待機へ戻る。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    monkeypatch.setattr(
        app._speech,
        "synthesize_response_stream",
        lambda deltas, slot_key="": iter([("text", "応答"), ("audio", b"WAV")]),
    )
    codes = []
    for _ in app._terminal_process_turn(b"RIFFDATA"):
        codes.append(app._dashboard_state.status_code())
    # 文字起こし中→読み上げ中を経由し、完了で待機(ready)へ戻る。
    assert "transcribing" in codes
    assert "speaking" in codes
    assert app._dashboard_state.status_code() == "ready"


def test_app_terminal_process_turn_empty_transcript(monkeypatch):
    """文字起こしが空ならerrorイベントを返す。"""
    _patch_app(monkeypatch)
    monkeypatch.setattr("argos.core.app.ArgosApp._transcribe_wav", lambda self, path: "")
    app = ArgosApp(_settings())
    events = list(app._terminal_process_turn(b"RIFFDATA"))
    assert events[-1]["event"] == "error"


def test_app_terminal_process_turn_locked_requires_auth(monkeypatch):
    """ロック中はエージェントを動かさず本人確認を促す。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    monkeypatch.setattr(app._auth_coord, "ensure_authenticated", lambda *a, **k: False)
    monkeypatch.setattr(app._auth_coord, "is_locked", lambda: True)
    called = {"agent": False}
    monkeypatch.setattr(
        app._speech,
        "synthesize_response_stream",
        lambda deltas, slot_key="": called.__setitem__("agent", True) or iter([]),
    )
    events = list(app._terminal_process_turn(b"RIFFDATA"))
    assert events[0]["event"] == "transcript"
    assert events[-1]["event"] == "error"
    assert "本人確認" in events[-1]["message"]
    assert called["agent"] is False
    # 本人確認用の発話はユーザ発話として記録しない。
    roles = [m["role"] for m in app._dashboard_state.snapshot()["messages"]]
    assert "user" not in roles


def test_app_terminal_process_turn_unlock_by_keyword(monkeypatch):
    """キーワードで解除できたら本人確認完了を返しエージェントは呼ばない。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    monkeypatch.setattr(app._auth_coord, "ensure_authenticated", lambda *a, **k: False)
    monkeypatch.setattr(app._auth_coord, "is_locked", lambda: False)
    events = list(app._terminal_process_turn(b"RIFFDATA"))
    kinds = [e["event"] for e in events]
    assert kinds == ["transcript", "text", "done"]
    assert events[1]["delta"] == "本人確認しました。"


def test_app_terminal_process_turn_agent_error(monkeypatch):
    """エージェント処理が例外を投げたらerrorイベントで終える。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())

    def _boom(deltas, slot_key=""):
        raise RuntimeError("agent down")
        yield  # pragma: no cover

    monkeypatch.setattr(app._speech, "synthesize_response_stream", _boom)
    events = list(app._terminal_process_turn(b"RIFFDATA"))
    assert events[0]["event"] == "transcript"
    assert events[-1]["event"] == "error"


def test_app_terminal_list_and_next_slots(monkeypatch):
    """スロット一覧と巡回切替が現在スロットを反映する。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    listing = app._terminal_list_slots()
    assert listing["current"]["name"] == "作業"
    assert "model" in listing["current"]
    assert listing["slots"][0]["active"] is True
    switched = app._terminal_next_slot()
    assert switched["current"]["name"] == "次"


def test_terminal_gateway_delegates(monkeypatch):
    """_TerminalGatewayが各操作を本体へ委譲する。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    gateway = _TerminalGateway(app)
    assert gateway.list_slots()["current"]["name"] == "作業"
    assert gateway.next_slot()["current"]["name"] == "次"


# --- SpeechController.synthesize_response_stream ---


def _speech_settings():
    """dry_run無効・VOICEVOX設定済みの合成テスト用Settings。"""
    return Settings(**{**_settings().__dict__, "dry_run": False, "tts_cache_enabled": False})


def test_synthesize_response_stream_yields_text_and_audio():
    """応答差分をテキストと文単位の合成WAVへ変換する。"""
    from argos.core.speech_controller import SpeechController

    class _Filter:
        def normalize(self, text):
            return f"norm:{text}"

    class _Voicevox:
        def synthesize(self, text, speaker=None):
            return text.encode("utf-8")

    settings = _speech_settings()
    speech = SpeechController(
        settings=settings,
        audio=None,
        lcd=None,
        tts_filter=_Filter(),
        voicevox=_Voicevox(),
        kokoro=None,
        tts_cache=None,
        dashboard_state=DashboardState(),
        status=None,
        voicevox_speakers_by_slot_key={},
        current_slot_key=lambda: "",
        is_current_slot=lambda key: True,
        report_error=lambda source, exc: None,
        shutdown=threading.Event(),
    )
    events = list(speech.synthesize_response_stream(iter(["こんにちは。", "元気？"])))
    kinds = [k for k, _ in events]
    assert kinds.count("text") == 2
    # 「こんにちは。」で区切られ1文分の音声が出る。末尾「元気？」はflushで音声化。
    audio = [payload for kind, payload in events if kind == "audio"]
    assert audio and audio[0] == "norm:こんにちは。".encode("utf-8")
