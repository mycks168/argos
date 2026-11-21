"""stt-gateway の /stream クライアントのテスト。"""

from __future__ import annotations

import json
import queue
import threading

import numpy as np
import pytest

from argos.services.stt.stream import (
    ARGOS_ACK_OPTIONS,
    build_start_options,
    OpusPacketEncoder,
    SttStreamClient,
    SttStreamError,
    SttStreamUtterance,
    _resample_pcm16,
    parse_bitrate,
    stream_url,
)

_CLOSE = object()


class FakeWebSocket:
    """stt-gateway の /stream を真似る、テスト用の WebSocket。"""

    def __init__(self, on_end=None, on_start=None):
        """end と start を受けたときに返すイベントを決める関数を保持する。"""
        self.sent: list[object] = []
        self.closed = False
        self._incoming: queue.Queue[object] = queue.Queue()
        self._on_end = on_end
        self._on_start = on_start
        self.ended = threading.Event()

    def push(self, event: dict) -> None:
        """サーバから届くイベントを積む。"""
        self._incoming.put(json.dumps(event, ensure_ascii=False))

    def send(self, message) -> None:
        """クライアントが送ったメッセージを記録し、start と end には応答する。"""
        self.sent.append(message)
        if isinstance(message, str):
            payload = json.loads(message)
            if payload["type"] == "start" and self._on_start is not None:
                self._on_start(self)
            if payload["type"] == "end":
                self.ended.set()
                if self._on_end is not None:
                    self._on_end(self)

    def close(self) -> None:
        """接続を閉じ、受信の繰り返しを終わらせる。"""
        self.closed = True
        self._incoming.put(_CLOSE)

    def __iter__(self):
        """届いたイベントを順に返す。"""
        while True:
            item = self._incoming.get()
            if item is _CLOSE:
                return
            yield item

    def packets(self) -> list[bytes]:
        """送った音声パケットを返す。"""
        return [message for message in self.sent if isinstance(message, bytes)]

    def texts(self) -> list[dict]:
        """送った JSON メッセージを返す。"""
        return [json.loads(message) for message in self.sent if isinstance(message, str)]


def _reply(transcript="今日の天気は", ack="天気ね、見てみるね。"):
    """end を受けたら transcript と ack を返す応答を作る。"""

    def on_end(ws):
        ws.push({"type": "transcript", "utterance_id": 1, "text": transcript, "latency_ms": 100})
        if transcript and ack is not None:
            ws.push({"type": "ack", "utterance_id": 1, "text": ack, "source": "llm"})

    return on_end


def _pcm(seconds: float, rate: int = 16000) -> bytes:
    """テスト用の正弦波の PCM16 を作る。"""
    count = int(seconds * rate)
    return (np.sin(np.arange(count) / 10) * 8000).astype("<i2").tobytes()


def _utterance(ws, **kwargs):
    """偽の WebSocket につながる発話を作って始める。"""
    calls = {}

    def connect(url, **options):
        """接続先と認証ヘッダを記録して、偽の WebSocket を返す。"""
        calls["url"] = url
        calls["options"] = options
        return ws

    utterance = SttStreamUtterance(
        url="ws://stt/stream",
        token=kwargs.pop("token", "secret"),
        language="ja",
        sample_rate=16000,
        bitrate=24000,
        connect=connect,
        **kwargs,
    )
    utterance.start()
    return utterance, calls


def test_parse_bitrate_accepts_k_suffix():
    """ビットレートの「k」表記を bps にする。"""
    assert parse_bitrate("24k") == 24000
    assert parse_bitrate("16000") == 16000
    assert parse_bitrate(32000) == 32000
    with pytest.raises(ValueError):
        parse_bitrate(" ")


def test_stream_url_converts_scheme():
    """http は ws、https は wss にして /stream を付ける。"""
    assert stream_url("http://stt:23000/") == "ws://stt:23000/stream"
    assert stream_url("https://stt.example") == "wss://stt.example/stream"
    assert stream_url("ws://stt") == "ws://stt/stream"


def test_resample_changes_length():
    """サンプリングレートの変換で、長さが比率どおりになる。"""
    data = _pcm(0.1, 44100)
    converted = _resample_pcm16(data, 44100, 16000)
    assert len(converted) // 2 == 1600
    assert _resample_pcm16(data, 16000, 16000) == data
    assert _resample_pcm16(b"", 44100, 16000) == b""


def test_opus_encoder_produces_decodable_packets():
    """Opus の生パケットを作り、48kHz でデコードし直せる。"""
    import av

    encoder = OpusPacketEncoder(16000, 24000)
    packets = encoder.encode(_pcm(0.5)[:-100]) + encoder.flush()
    assert packets
    decoder = av.CodecContext.create("libopus", "r")
    decoder.sample_rate = 48000
    decoder.layout = "mono"
    samples = sum(frame.samples for packet in packets for frame in decoder.decode(av.Packet(packet)))
    assert samples >= 0.5 * 48000 * 0.9


def test_opus_encoder_resamples_unsupported_rate():
    """libopus が受け付けないレートは 16kHz にしてから符号化する。"""
    encoder = OpusPacketEncoder(44100, 24000)
    assert encoder.encode(_pcm(0.2, 44100))


def test_utterance_streams_audio_and_returns_transcript_and_ack():
    """音声を Opus で送り、文字起こしと最初の返事を受け取る。"""
    ws = FakeWebSocket(on_end=_reply())
    utterance, calls = _utterance(ws)
    utterance.feed(_pcm(0.3))

    assert utterance.finish(timeout=5) == "今日の天気は"
    assert utterance.wait_ack(5) == "天気ね、見てみるね。"
    assert calls["url"] == "ws://stt/stream"
    assert calls["options"]["additional_headers"] == {"Authorization": "Bearer secret"}
    assert ws.texts()[0] == {"type": "start", "encoding": "opus", "language": "ja"}
    assert ws.texts()[-1] == {"type": "end"}
    assert ws.packets()
    assert ws.closed


def test_utterance_without_token_sends_no_auth_header():
    """トークンが空なら認証ヘッダを付けない。"""
    ws = FakeWebSocket(on_end=_reply())
    utterance, calls = _utterance(ws, token="")
    assert utterance.finish(timeout=5)
    assert calls["options"]["additional_headers"] == {}
    utterance.close()


def test_empty_transcript_closes_without_ack():
    """文字起こしが空なら ack を待たずに閉じる。"""
    ws = FakeWebSocket(on_end=_reply(transcript=""))
    utterance, _ = _utterance(ws)
    assert utterance.finish(timeout=5) == ""
    assert utterance.wait_ack(5) is None
    assert ws.closed


def test_wait_ack_times_out():
    """ack が来なければ None を返して閉じる。"""
    ws = FakeWebSocket(on_end=_reply(ack=None))
    utterance, _ = _utterance(ws)
    assert utterance.finish(timeout=5) == "今日の天気は"
    assert utterance.wait_ack(0.05) is None
    assert ws.closed


def test_connect_failure_raises_on_finish():
    """接続できなければ finish で SttStreamError を送出する。"""

    def connect(url, **options):
        """接続に失敗する。"""
        raise OSError("refused")

    utterance = SttStreamUtterance(
        url="ws://stt/stream", token="", language="ja", sample_rate=16000, bitrate=24000, connect=connect
    )
    utterance.start()
    utterance.feed(_pcm(0.1))
    with pytest.raises(SttStreamError, match="接続できません"):
        utterance.finish(timeout=5)
    assert utterance.failed


def test_fatal_error_event_raises_on_finish():
    """transcribe_failed を受けたら finish で SttStreamError を送出する。"""

    def on_end(ws):
        """文字起こしの失敗を返す。"""
        ws.push({"type": "error", "utterance_id": 1, "code": "transcribe_failed", "detail": "boom"})

    ws = FakeWebSocket(on_end=on_end)
    utterance, _ = _utterance(ws)
    with pytest.raises(SttStreamError, match="transcribe_failed"):
        utterance.finish(timeout=5)


def test_non_fatal_error_and_unreadable_events_are_ignored():
    """続けられる error や読めないイベントは無視して、文字起こしを待つ。"""

    def on_end(ws):
        """読めないイベントと続けられる error を送ってから、結果を返す。"""
        ws._incoming.put("not json")
        ws._incoming.put(b"binary")
        ws.push({"type": "error", "utterance_id": 1, "code": "invalid_audio", "detail": "bad"})
        _reply()(ws)

    ws = FakeWebSocket(on_end=on_end)
    utterance, _ = _utterance(ws)
    assert utterance.finish(timeout=5) == "今日の天気は"
    utterance.close()


def test_connection_closed_before_transcript_raises():
    """文字起こしの前に接続が切れたら SttStreamError を送出する。"""
    ws = FakeWebSocket(on_end=lambda ws: ws.close())
    utterance, _ = _utterance(ws)
    with pytest.raises(SttStreamError, match="接続が切れました"):
        utterance.finish(timeout=5)


def test_finish_times_out():
    """文字起こしが届かなければ時間切れで SttStreamError を送出する。"""
    ws = FakeWebSocket()
    utterance, _ = _utterance(ws)
    with pytest.raises(SttStreamError, match="届きませんでした"):
        utterance.finish(timeout=0.05)
    assert ws.closed


def test_send_failure_marks_failed():
    """送信に失敗したら失敗として扱う。"""

    class BrokenWebSocket(FakeWebSocket):
        """音声の送信に失敗する WebSocket。"""

        def send(self, message):
            """バイナリの送信だけ失敗させる。"""
            if isinstance(message, bytes):
                raise OSError("broken pipe")
            super().send(message)

    ws = BrokenWebSocket()
    utterance, _ = _utterance(ws)
    utterance.feed(_pcm(0.3))
    with pytest.raises(SttStreamError, match="送信に失敗"):
        utterance.finish(timeout=5)


def test_backchannel_only_while_recording_and_after_live_start():
    """相槌は、発話の開始より後の位置で、録音中に届いたものだけ渡す。"""
    received: list[str] = []
    got = threading.Event()

    def on_backchannel(text):
        """受け取った相槌を記録する。"""
        received.append(text)
        got.set()

    ws = FakeWebSocket(on_end=_reply())
    utterance, _ = _utterance(ws, on_backchannel=on_backchannel)
    # 先読み分（0.5秒）は live=False で送る。
    utterance.feed(_pcm(0.5), live=False)
    utterance.feed(_pcm(0.5))
    ws.push({"type": "started", "utterance_id": 1, "backchannel": False, "backchannel_unavailable_reason": "busy"})
    ws.push({"type": "backchannel", "utterance_id": 1, "text": "うん", "offset_ms": 200, "probability": 0.8})
    ws.push({"type": "backchannel", "utterance_id": 1, "text": "", "offset_ms": 700, "probability": 0.8})
    ws.push({"type": "backchannel", "utterance_id": 1, "text": "なるほど", "offset_ms": 700, "probability": 0.8})
    assert got.wait(5)
    assert utterance.finish(timeout=5)
    ws.push({"type": "backchannel", "utterance_id": 1, "text": "はい", "offset_ms": 900, "probability": 0.7})
    utterance.close()
    assert received == ["なるほど"]
    summary = utterance.debug_summary()
    assert summary["audio_ms"] == 1000
    assert summary["live_from_ms"] == 500
    assert summary["transcript"] == "今日の天気は"
    assert summary["backchannel_available"] is False
    assert summary["backchannel_unavailable_reason"] == "busy"
    assert [event["result"] for event in summary["backchannels"][:3]] == ["pre_roll", "empty", "delivered"]
    assert summary["backchannels"][2]["lag_ms"] == 300


def test_backchannel_callback_errors_are_logged():
    """相槌の呼び出し先が失敗しても、受信は続ける。"""

    def on_backchannel(text):
        """失敗する呼び出し先。"""
        raise RuntimeError("boom")

    ws = FakeWebSocket(on_end=_reply())
    utterance, _ = _utterance(ws, on_backchannel=on_backchannel)
    utterance.feed(_pcm(0.2))
    ws.push({"type": "backchannel", "utterance_id": 1, "text": "うん", "offset_ms": 100, "probability": 0.8})
    assert utterance.finish(timeout=5) == "今日の天気は"
    utterance.close()


def test_feed_after_close_is_ignored_and_close_is_idempotent():
    """閉じた後の音声は捨て、close は何度呼んでもよい。"""
    ws = FakeWebSocket()
    utterance, _ = _utterance(ws)
    utterance.close()
    utterance.close()
    utterance.feed(_pcm(0.1))
    assert ws.ended.wait(0.2) is False


def test_close_before_connect_closes_new_connection():
    """接続を待つ間に閉じたら、つながった接続もすぐ閉じる。"""
    ws = FakeWebSocket()
    release = threading.Event()

    def connect(url, **options):
        """閉じられるまで接続を遅らせる。"""
        release.wait(5)
        return ws

    utterance = SttStreamUtterance(
        url="ws://stt/stream", token="", language="ja", sample_rate=16000, bitrate=24000, connect=connect
    )
    utterance.start()
    utterance.close()
    release.set()
    utterance._sender.join(5)
    assert ws.closed
    assert ws.sent == []


def test_client_open_builds_utterance():
    """クライアントは、設定どおりの発話を作って始める。"""
    ws = FakeWebSocket(on_end=_reply())
    seen = {}

    def connect(url, **options):
        """接続先を記録する。"""
        seen["url"] = url
        return ws

    client = SttStreamClient("http://stt:23000", "ja", "token", "16k", connect=connect)
    utterance = client.open(16000)
    utterance.feed(_pcm(0.1))
    assert utterance.finish(timeout=5) == "今日の天気は"
    utterance.close()
    assert seen["url"] == "ws://stt:23000/stream"


def test_build_start_options():
    """ack と backchannel の指定を作る。ack を使わないなら false にする。"""
    options = build_start_options(ack_enabled=True, backchannel_phrases=("うん",), backchannel_min_interval_ms=1500)
    assert options["ack"] == ARGOS_ACK_OPTIONS
    assert options["ack"] is not ARGOS_ACK_OPTIONS
    assert options["backchannel"] == {"min_interval_ms": 1500, "phrases": ["うん"]}
    without = build_start_options(ack_enabled=False, backchannel_phrases=(), backchannel_min_interval_ms=0)
    assert without == {"ack": False, "backchannel": {"min_interval_ms": 0}}


def test_argos_ack_prompt_keeps_listener_wording():
    """返事のプロンプトは「聞き手」「答えを知らない」「日本語で」を保ち、繰り返しと聞き返しを禁じる。"""
    prompt = ARGOS_ACK_OPTIONS["system_prompt"]
    for phrase in ("会話の聞き手", "答えを知らない", "日本語で", "繰り返しません", "聞き返しもしません"):
        assert phrase in prompt
    assert len(prompt) <= 2000
    assert 0 < len(ARGOS_ACK_OPTIONS["examples"]) <= 8
    assert len(ARGOS_ACK_OPTIONS["fallback_text"]) <= 50


def test_start_message_includes_options():
    """start に指定を付けて送る。"""
    ws = FakeWebSocket(on_end=_reply())
    options = {"ack": False, "backchannel": {"threshold": 0.4, "min_interval_ms": 1500}}
    utterance, _ = _utterance(ws, start_options=options)
    assert utterance.finish(timeout=5)
    utterance.close()
    assert ws.texts()[0] == {"type": "start", "encoding": "opus", "language": "ja", **options}


def test_invalid_message_before_started_fails_fast():
    """start が受け付けられなかった（started の前の invalid_message）ら、待たずに失敗にする。"""
    ws = FakeWebSocket(
        on_start=lambda ws: ws.push(
            {"type": "error", "utterance_id": None, "code": "invalid_message", "detail": "unknown field"}
        )
    )
    utterance, _ = _utterance(ws)
    with pytest.raises(SttStreamError, match="invalid_message"):
        utterance.finish(timeout=5)


def test_invalid_message_after_started_is_ignored():
    """発話が始まった後の invalid_message では失敗にしない。"""

    def on_start(ws):
        """発話を始めてから、続けられる error を送る。"""
        ws.push({"type": "started", "utterance_id": 1, "backchannel": True, "backchannel_unavailable_reason": None})
        ws.push({"type": "error", "utterance_id": 1, "code": "invalid_message", "detail": "bad json"})

    ws = FakeWebSocket(on_end=_reply(), on_start=on_start)
    utterance, _ = _utterance(ws)
    assert utterance.finish(timeout=5) == "今日の天気は"
    utterance.close()


def test_backchannel_too_early_is_not_delivered():
    """話し始めてから決めた時間までに届いた相槌は鳴らさない。先読み分は話し始めに数えない。"""
    received: list[str] = []
    got = threading.Event()

    def on_backchannel(text):
        """受け取った相槌を記録する。"""
        received.append(text)
        got.set()

    ws = FakeWebSocket(on_end=_reply())
    utterance, _ = _utterance(ws, on_backchannel=on_backchannel, backchannel_start_delay_ms=2000)
    utterance.feed(_pcm(0.5), live=False)
    utterance.feed(_pcm(3.0))
    # 話し始め（500ms）から 1.8 秒後は早すぎる。2.5 秒後は鳴らす。
    ws.push({"type": "backchannel", "utterance_id": 1, "text": "うん", "offset_ms": 2300, "probability": 0.5})
    ws.push({"type": "backchannel", "utterance_id": 1, "text": "そっか", "offset_ms": 3000, "probability": 0.5})
    assert got.wait(5)
    assert utterance.finish(timeout=5)
    utterance.close()
    assert received == ["そっか"]
    assert [event["result"] for event in utterance.debug_summary()["backchannels"]] == ["too_early", "delivered"]


def test_client_passes_start_delay():
    """クライアントは、話し始めてから相槌を鳴らさない時間を発話へ渡す。"""
    ws = FakeWebSocket(on_end=_reply())
    client = SttStreamClient("http://stt", "ja", connect=lambda url, **options: ws, backchannel_start_delay_ms=1500)
    utterance = client.open(16000)
    assert utterance._backchannel_start_delay_ms == 1500
    utterance.feed(_pcm(0.1))
    assert utterance.finish(timeout=5)
    utterance.close()


def test_backchannel_log_and_summary(caplog):
    """届いた相槌を調査用JSONとログの両方へ残す。"""
    utterance = SttStreamUtterance(url="ws://stt", token="", language="ja", sample_rate=16000, bitrate=24000)
    utterance.feed(_pcm(3))
    with caplog.at_level("INFO"):
        utterance._handle_event({"type": "backchannel", "text": "うん", "offset_ms": 2500})
    assert utterance.debug_summary()["backchannels"][0]["text"] == "うん"
    assert "相槌を受信: うん" in caplog.text


def test_build_start_options_silence_ms():
    """無音の長さは指定したときだけ送り、gatewayが拒否する項目は送らない。"""
    base = dict(ack_enabled=True, backchannel_phrases=("うん",), backchannel_min_interval_ms=2500)
    plain = build_start_options(**base)
    assert set(plain["backchannel"]) == {"phrases", "min_interval_ms"}
    timed = build_start_options(**base, backchannel_silence_ms=120)
    assert timed["backchannel"]["silence_ms"] == 120
    assert not {"threshold", "timing", "phrases_by_kind"} & set(timed["backchannel"])
