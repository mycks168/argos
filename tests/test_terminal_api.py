"""PiZero端末API（Terminal API）と関連処理のテスト。"""

import base64
import json
import threading
from urllib.error import HTTPError
from urllib.parse import urlencode
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
        self.received_text = None
        self.want_audio = None
        self.slot_name = ""
        self.slot_provider = ""

    def list_slots(self):
        return {"slots": [{"name": "作業", "provider": "codex", "active": True}], "current": {"name": "作業", "provider": "codex"}}

    def next_slot(self):
        return {"slots": [{"name": "次", "provider": "antigravity", "active": True}], "current": {"name": "次", "provider": "antigravity"}}

    def select_slot(self, name, provider):
        if name == "なし":
            raise ValueError("エージェントスロットが見つかりません")
        return {"slots": [{"name": name, "provider": provider, "active": True}], "current": {"name": name, "provider": provider}}

    def slot_history(self, name, provider):
        """指定スロットの履歴を返す。"""
        if name == "なし":
            raise ValueError("エージェントスロットが見つかりません")
        return {"messages": [{"role": "assistant", "text": f"{name}:{provider}"}]}

    def process_turn(
        self,
        wav_bytes=None,
        *,
        text=None,
        want_audio=True,
        slot_name="",
        slot_provider="",
    ):
        self.received_wav = wav_bytes
        self.received_text = text
        self.want_audio = want_audio
        self.slot_name = slot_name
        self.slot_provider = slot_provider
        yield from self._turn_events


def _start_server(handler, token="secret"):
    """端末ハンドラ付きのDashboardServerを起動しベースURLを返す。"""
    server = DashboardServer(DashboardState(), "127.0.0.1", 0, token, terminal_handler=handler)
    server.start()
    base_url = f"http://{server.address[0]}:{server.address[1]}"
    return server, base_url


def _request(url, method="GET", body=None, token="secret", content_type="application/json", accept=""):
    """Terminal APIへHTTPリクエストを送りレスポンスを返す。"""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None and content_type:
        headers["Content-Type"] = content_type
    if accept:
        headers["Accept"] = accept
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


def test_terminal_history_returns_selected_slot_messages():
    """履歴APIは指定スロットの会話をBearer認証付きで返す。"""
    handler = FakeTerminalHandler([])
    server, base_url = _start_server(handler)
    try:
        query = urlencode({"name": "作業", "provider": "codex"})
        with _request(base_url + f"/api/terminal/history?{query}") as response:
            payload = json.loads(response.read())
        assert payload["messages"] == [{"role": "assistant", "text": "作業:codex"}]
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


def test_terminal_select_slot_by_name_and_provider():
    """スロット選択APIは名前とproviderを指定して切り替える。"""
    handler = FakeTerminalHandler([])
    server, base_url = _start_server(handler)
    try:
        body = json.dumps({"name": "調査", "provider": "claude"}).encode()
        with _request(base_url + "/api/terminal/slots/select", method="POST", body=body) as response:
            payload = json.loads(response.read())
        assert payload["current"] == {"name": "調査", "provider": "claude"}
    finally:
        server.stop()


def test_terminal_select_slot_rejects_unknown_slot():
    """存在しないスロットの選択は400を返す。"""
    handler = FakeTerminalHandler([])
    server, base_url = _start_server(handler)
    try:
        body = json.dumps({"name": "なし", "provider": "codex"}).encode()
        with pytest.raises(HTTPError) as exc:
            _request(base_url + "/api/terminal/slots/select", method="POST", body=body)
        assert exc.value.code == 400
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


def test_terminal_turn_targets_explicit_slot_without_changing_legacy_body():
    """任意指定されたスロットを同じターンAPIからハンドラへ渡す。"""
    handler = FakeTerminalHandler([{"event": "done", "text": "了解"}])
    server, base_url = _start_server(handler)
    try:
        query = urlencode({"name": "調査", "provider": "codex"})
        with _request(
            base_url + f"/api/terminal/turn?{query}",
            method="POST",
            body="確認".encode(),
            content_type="text/plain; charset=utf-8",
            accept="text/event-stream",
        ) as response:
            assert _read_sse_events(response)[-1][0] == "done"
        assert handler.received_text == "確認"
        assert handler.slot_name == "調査"
        assert handler.slot_provider == "codex"
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


def test_terminal_turn_accepts_text_and_requests_text_response():
    """テキスト入力と文字回答指定を端末ハンドラへ渡す。"""
    handler = FakeTerminalHandler([{"event": "done", "text": "了解"}])
    server, base_url = _start_server(handler)
    try:
        with _request(
            base_url + "/api/terminal/turn",
            method="POST",
            body="こんにちは".encode(),
            content_type="text/plain; charset=utf-8",
            accept="text/event-stream",
        ) as response:
            assert _read_sse_events(response)[-1][0] == "done"
        assert handler.received_wav is None
        assert handler.received_text == "こんにちは"
        assert handler.want_audio is False
    finally:
        server.stop()


def test_terminal_turn_rejects_unsupported_content_type():
    """未対応の入力形式は415で拒否する。"""
    handler = FakeTerminalHandler([])
    server, base_url = _start_server(handler)
    try:
        with pytest.raises(HTTPError) as exc:
            _request(base_url + "/api/terminal/turn", method="POST", body=b"{}", content_type="application/json")
        assert exc.value.code == 415
    finally:
        server.stop()


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
    snapshot = app._dashboard_state.snapshot()
    roles = [(m["role"], m["text"]) for m in snapshot["messages"]]
    assert ("user", "こんにちは") in roles
    assert ("assistant", "応答") in roles
    # 端末PTTでキオスク画面のスクリーンセーバーが解除される。
    assert snapshot["display_activity"]["sequence"] >= 1


def test_app_terminal_process_text_turn_skips_stt_and_tts(monkeypatch):
    """文字入出力ではSTTとTTSを呼ばず、応答差分を記録する。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    monkeypatch.setattr(app, "_transcribe_wav", lambda path: pytest.fail("STTは呼ばない"))
    monkeypatch.setattr(app._speech, "synthesize_response_stream", lambda *args: pytest.fail("TTSは呼ばない"))
    events = list(app._terminal_process_turn(text=" 質問です ", want_audio=False))
    assert [event["event"] for event in events] == ["transcript", "text", "done"]
    assert events[0]["text"] == "質問です"
    assert events[-1]["text"] == "応答"


def test_app_terminal_process_turn_targets_slot_without_selecting(monkeypatch):
    """明示指定ターンは現在選択を変えず対象スロットへ送る。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())

    events = list(
        app._terminal_process_turn(
            text="対象へ送信",
            want_audio=False,
            slot_name="作業",
            slot_provider="codex",
        )
    )

    assert events[-1] == {"event": "done", "text": "応答"}
    assert app._agent.targeted == [("作業", "codex", "対象へ送信")]
    assert app._agent.current_name == "作業"


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
    selected = app._terminal_select_slot("作業", "codex")
    assert selected["current"]["name"] == "作業"


def test_terminal_slot_switch_resets_old_slot_status(monkeypatch):
    """処理中に別スロットへ切り替えると選択先は待機表示になる。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    token = app._status.current_generation()
    app._status.set(token, "thinking", "考え中")

    app._terminal_next_slot()

    assert app._dashboard_state.status_code() == "ready"


def test_terminal_turn_marks_completed_background_slot_unread(monkeypatch):
    """切替後に元スロットの文字回答が完了すると未読表示にする。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())

    def switch_then_respond(prompt):
        """応答中に別スロットへ切り替えて回答を返す。"""
        app._terminal_next_slot()
        yield "裏で完了"

    monkeypatch.setattr(app._agent, "ask_stream", switch_then_respond)
    events = list(app._terminal_process_turn(text="質問", want_audio=False))

    assert events[-1] == {"event": "done", "text": "裏で完了"}
    slots = {(slot["provider"], slot["name"]): slot for slot in app._dashboard_state.snapshot()["slots"]}
    assert slots[("codex", "作業")]["unread"] is True
    assert app._dashboard_state.status_code() == "ready"


def test_terminal_gateway_delegates(monkeypatch):
    """_TerminalGatewayが各操作を本体へ委譲する。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    gateway = _TerminalGateway(app)
    assert gateway.list_slots()["current"]["name"] == "作業"
    assert gateway.next_slot()["current"]["name"] == "次"
    assert gateway.select_slot("作業", "codex")["current"]["name"] == "作業"


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


# --- Opus対応 ---
def _make_test_wav(seconds: float = 0.2) -> bytes:
    """テスト用の小さな正弦波WAVを生成する。"""
    import io
    import math
    import struct
    import wave

    frames = int(16000 * seconds)
    buf = io.BytesIO()
    with wave.open(buf, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"".join(struct.pack("<h", int(10000 * math.sin(2 * math.pi * 440 * i / 16000))) for i in range(frames)))
    return buf.getvalue()


def test_terminal_turn_accepts_opus_upload():
    """Content-TypeがOgg Opusの音声はWAVへデコードしてから処理する。"""
    from argos.services.opus_codec import encode_wav_to_opus

    wav = _make_test_wav()
    opus = encode_wav_to_opus(wav)
    assert len(opus) < len(wav)
    handler = FakeTerminalHandler([{"event": "done", "text": "OK"}])
    server, base_url = _start_server(handler)
    try:
        with _request(base_url + "/api/terminal/turn", method="POST", body=opus, content_type="audio/ogg") as response:
            events = _read_sse_events(response)
        assert [name for name, _ in events] == ["done"]
        assert handler.received_wav.startswith(b"RIFF")
    finally:
        server.stop()


def test_terminal_turn_accepts_browser_mp4_audio(monkeypatch):
    """Safariのaudio/mp4録音はWAVへ変換してから処理する。"""
    monkeypatch.setattr("argos.services.dashboard.server.decode_audio_to_wav", lambda data: b"RIFF" + data)
    handler = FakeTerminalHandler([{"event": "done", "text": "OK"}])
    server, base_url = _start_server(handler)
    try:
        with _request(
            base_url + "/api/terminal/turn",
            method="POST",
            body=b"MP4DATA",
            content_type="audio/mp4",
        ) as response:
            assert _read_sse_events(response)[-1][0] == "done"
        assert handler.received_wav == b"RIFFMP4DATA"
    finally:
        server.stop()


def test_terminal_turn_rejects_broken_opus():
    """Opusとして解釈できないボディは400を返す。"""
    handler = FakeTerminalHandler([])
    server, base_url = _start_server(handler)
    try:
        with pytest.raises(HTTPError) as exc_info:
            _request(base_url + "/api/terminal/turn", method="POST", body=b"not-opus", content_type="audio/ogg")
        assert exc_info.value.code == 400
    finally:
        server.stop()


def test_terminal_turn_returns_opus_audio_when_requested():
    """X-Argos-Audio: opus のとき応答音声チャンクをOpusで返す。"""
    from argos.services.opus_codec import decode_opus_to_wav

    wav = _make_test_wav()
    turn_events = [
        {"event": "audio", "seq": 0, "format": "wav", "data": base64.b64encode(wav).decode("ascii")},
        {"event": "done", "text": "OK"},
    ]
    handler = FakeTerminalHandler(turn_events)
    server, base_url = _start_server(handler)
    try:
        request = Request(
            base_url + "/api/terminal/turn",
            data=b"RIFFDATA",
            headers={
                "Authorization": "Bearer secret",
                "Content-Type": "audio/wav",
                "X-Argos-Audio": "opus",
            },
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            events = _read_sse_events(response)
        audio_events = [payload for name, payload in events if name == "audio"]
        assert audio_events[0]["format"] == "opus"
        decoded = decode_opus_to_wav(base64.b64decode(audio_events[0]["data"]))
        assert decoded.startswith(b"RIFF")
    finally:
        server.stop()


def test_terminal_turn_keeps_wav_audio_by_default():
    """Opus要求ヘッダが無ければ応答音声はWAVのまま返す。"""
    turn_events = [
        {"event": "audio", "seq": 0, "format": "wav", "data": base64.b64encode(b"WAVDATA").decode("ascii")},
        {"event": "done", "text": "OK"},
    ]
    handler = FakeTerminalHandler(turn_events)
    server, base_url = _start_server(handler)
    try:
        with _request(base_url + "/api/terminal/turn", method="POST", body=b"RIFFDATA", content_type="audio/wav") as response:
            events = _read_sse_events(response)
        audio_events = [payload for name, payload in events if name == "audio"]
        assert audio_events[0]["format"] == "wav"
        assert base64.b64decode(audio_events[0]["data"]) == b"WAVDATA"
    finally:
        server.stop()
