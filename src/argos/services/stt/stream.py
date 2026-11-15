"""stt-gateway の WebSocket /stream クライアント。

録音しながら音声を Opus の生パケットにして送り、話している最中の相槌（backchannel）、
文字起こし結果（transcript）、最初の返事（ack）を受け取る。1 つの発話ごとに
1 本の接続を張り、発話が終わって ack を受け取ったら閉じる。
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

import numpy as np

from argos.config import DEFAULT_STT_BACKCHANNEL_PHRASES

log = logging.getLogger(__name__)

# libopus がそのまま受け付けるサンプリングレート。これ以外は 16kHz に変換してから送る。
OPUS_SAMPLE_RATES = (8000, 12000, 16000, 24000, 48000)
OPUS_FALLBACK_SAMPLE_RATE = 16000
TRANSCRIPT_TIMEOUT_SECONDS = 30.0
CONNECT_TIMEOUT_SECONDS = 3.0
# この error を受けたら、その発話の文字起こしは得られない。
FATAL_ERROR_CODES = frozenset({"transcribe_failed", "utterance_too_long", "unsupported_language", "invalid_state"})
_END = object()

# 最初の返事（ack）の作り方。ARGOS では ack の後にエージェントの本回答が続くため、
# 内容を受け止める（言葉を繰り返す・聞き返す）と本回答と同じ受け止めが 2 回続いて不自然になる。
# そこで中身のない「つなぎ」の一言にする。stt-gateway の README の推奨（本回答が後に続く
# クライアント向け）のまま。「聞き手」「答えを知らない」「日本語で」を外すと、小型の LLM が
# 質問に答えて言い切ったり、日本語以外で返したりする（stt-gateway の変更履歴 2026-11-15）。
ARGOS_ACK_OPTIONS: dict[str, Any] = {
    "system_prompt": (
        "あなたは会話の聞き手です。相手の話を聞いて、相槌を一言だけ打ちます。"
        "あなたは答えを知らないので、質問や依頼には答えず「ちょっと見てみるね」「考えるね」のように"
        "受け止めるだけにします。相手の言葉は繰り返しません。聞き返しもしません。"
        "日本語で、15文字以内、くだけた口調、1文だけ。"
    ),
    "examples": [
        {"user": "明日の天気ってどう？", "reply": "ちょっと見てみるね。"},
        {"user": "なんか最近調子悪いんだよね", "reply": "そっか、ちょっと考えるね。"},
        {"user": "この関数どこで呼ばれてるんだっけ", "reply": "うん、探してみる。"},
        {"user": "お疲れさま、今日はもういいよ", "reply": "うん、お疲れさま！"},
    ],
    "fallback_text": "えっと、ちょっと待ってね。",
}


def build_start_options(
    *,
    ack_enabled: bool,
    backchannel_phrases: tuple[str, ...],
    backchannel_threshold: float,
    backchannel_min_interval_ms: int,
    backchannel_phrases_by_kind: tuple[tuple[str, tuple[str, ...]], ...] = (),
) -> dict[str, Any]:
    """start に付ける、発話ごとの ack と backchannel の指定を作る。

    ack を読み上げない設定（進捗音声がオフ）のときは "ack": false にして、
    stt-gateway に LLM で返事を作らせない。
    """
    backchannel: dict[str, Any] = {
        "threshold": backchannel_threshold,
        "min_interval_ms": backchannel_min_interval_ms,
    }
    if backchannel_phrases_by_kind:
        # 文言が1つなら文字列、複数なら配列で送る。配列は、その種類の中で順に使われる。
        backchannel["phrases_by_kind"] = {
            kind: phrases[0] if len(phrases) == 1 else list(phrases) for kind, phrases in backchannel_phrases_by_kind
        }
    elif backchannel_phrases:
        backchannel["phrases"] = list(backchannel_phrases)
    return {
        "ack": json.loads(json.dumps(ARGOS_ACK_OPTIONS)) if ack_enabled else False,
        "backchannel": backchannel,
    }


class SttStreamError(RuntimeError):
    """ストリーミング文字起こしで結果を得られなかったときに送出する例外。"""


def parse_bitrate(value: str | int) -> int:
    """"24k" のようなビットレート表記を bps の整数に変換する。"""
    text = str(value).strip().lower()
    if not text:
        raise ValueError("ビットレートが空です")
    if text.endswith("k"):
        return int(float(text[:-1]) * 1000)
    return int(text)


def stream_url(base_url: str) -> str:
    """stt-gateway のベース URL から /stream の WebSocket URL を作る。"""
    base = base_url.strip().rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    return f"{base}/stream"


def _resample_pcm16(data: bytes, from_rate: int, to_rate: int) -> bytes:
    """PCM16 モノラルを線形補間で別のサンプリングレートに変換する。"""
    if from_rate == to_rate:
        return data
    samples = np.frombuffer(data[: len(data) - (len(data) % 2)], dtype="<i2")
    if samples.size == 0:
        return b""
    target_size = max(1, int(round(samples.size * to_rate / from_rate)))
    source_index = np.arange(samples.size, dtype=np.float64)
    target_index = np.linspace(0, samples.size - 1, num=target_size)
    converted = np.interp(target_index, source_index, samples.astype(np.float64))
    return np.clip(converted, -32768, 32767).astype("<i2").tobytes()


class OpusPacketEncoder:
    """PCM16 モノラルを Opus の生パケット（コンテナなし）に変換する。

    Opus は決まった長さ（20ms）のフレーム単位でしか符号化できないため、
    半端な音声は次の入力まで持ち越し、flush() で無音を足して出し切る。
    """

    def __init__(self, sample_rate: int, bitrate: int) -> None:
        """入力のサンプリングレートとビットレートから、エンコーダを用意する。"""
        import av

        self._input_rate = int(sample_rate)
        self._rate = self._input_rate if self._input_rate in OPUS_SAMPLE_RATES else OPUS_FALLBACK_SAMPLE_RATE
        codec = av.CodecContext.create("libopus", "w")
        codec.sample_rate = self._rate
        codec.layout = "mono"
        codec.format = "s16"
        codec.bit_rate = int(bitrate)
        codec.open()
        self._av = av
        self._codec = codec
        self._frame_size = int(codec.frame_size or self._rate // 50)
        self._pending = np.zeros(0, dtype="<i2")
        self._pts = 0

    def encode(self, pcm: bytes) -> list[bytes]:
        """PCM16 を足し、符号化できたパケットを順に返す。"""
        data = _resample_pcm16(pcm, self._input_rate, self._rate)
        if data:
            self._pending = np.concatenate([self._pending, np.frombuffer(data, dtype="<i2")])
        packets: list[bytes] = []
        while self._pending.size >= self._frame_size:
            chunk, self._pending = self._pending[: self._frame_size], self._pending[self._frame_size :]
            packets.extend(self._encode_frame(chunk))
        return packets

    def flush(self) -> list[bytes]:
        """持ち越した音声に無音を足して符号化し、エンコーダに残ったパケットも出し切る。"""
        packets: list[bytes] = []
        if self._pending.size:
            padded = np.zeros(self._frame_size, dtype="<i2")
            padded[: self._pending.size] = self._pending
            self._pending = np.zeros(0, dtype="<i2")
            packets.extend(self._encode_frame(padded))
        packets.extend(bytes(packet) for packet in self._codec.encode(None))
        return packets

    def _encode_frame(self, samples: np.ndarray) -> list[bytes]:
        """1 フレーム分の音声を符号化する。"""
        frame = self._av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = self._rate
        frame.pts = self._pts
        self._pts += samples.size
        return [bytes(packet) for packet in self._codec.encode(frame)]


class SttStreamUtterance:
    """1 つの発話について、音声の送信とイベントの受信を進める。

    feed() は録音のスレッドから呼ばれるため、すぐに戻る（キューへ積むだけ）。
    接続と送信は送信用スレッドで、イベントの受信は受信用スレッドで行う。
    """

    def __init__(
        self,
        *,
        url: str,
        token: str,
        language: str,
        sample_rate: int,
        bitrate: int,
        on_backchannel: Callable[[str], None] | None = None,
        connect: Callable[..., Any] | None = None,
        encoder_factory: Callable[[int, int], OpusPacketEncoder] = OpusPacketEncoder,
        start_options: dict[str, Any] | None = None,
        backchannel_start_delay_ms: float = 0.0,
        on_kind_unsupported: Callable[[], None] | None = None,
        backchannel_fallback_phrases: tuple[str, ...] = DEFAULT_STT_BACKCHANNEL_PHRASES,
    ) -> None:
        """接続先と音声の形式、相槌を受け取ったときの呼び出し先、start に付ける指定を保持する。

        backchannel_start_delay_ms は、話し始めてから相槌を鳴らさない時間。予測モデルは話し始めの
        1 秒ほどでも相槌を打つべきと判断することがあり、話し始めた直後の「うん」は割り込みに聞こえるため。
        """
        self._url = url
        self._token = token
        self._language = language
        self._sample_rate = int(sample_rate)
        self._bitrate = int(bitrate)
        self._on_backchannel = on_backchannel
        self._connect = connect
        self._encoder_factory = encoder_factory
        self._start_options = dict(start_options or {})
        self._backchannel_start_delay_ms = max(0.0, float(backchannel_start_delay_ms))
        self._started = False
        self._start_ready = threading.Event()
        self._kind_rejected = False
        self._on_kind_unsupported = on_kind_unsupported
        self._backchannel_fallback_phrases = backchannel_fallback_phrases
        self._queue: queue.Queue[object] = queue.Queue()
        self._lock = threading.Lock()
        self._ws: Any = None
        self._closed = False
        self._ended = False
        self._error: Exception | None = None
        self._transcript: str | None = None
        self._ack: str | None = None
        self._transcript_ready = threading.Event()
        self._ack_ready = threading.Event()
        # 送った音声の長さ（ミリ秒）。録音前（ウェイクワードの前など）の音声への相槌を除くために使う。
        self._fed_ms = 0.0
        self._live_from_ms: float | None = None
        self._sender: threading.Thread | None = None
        # 調査用の記録。届いた相槌と、この発話で相槌を使えたか。
        self._backchannel_events: list[dict[str, Any]] = []
        self._backchannel_available: bool | None = None
        self._backchannel_unavailable_reason: str | None = None

    @property
    def failed(self) -> bool:
        """接続や送信に失敗して、文字起こしを得られない状態ならTrueを返す。"""
        return self._error is not None

    def debug_summary(self) -> dict[str, Any]:
        """調査用に、この発話の結果と、届いた相槌の記録を返す。"""
        return {
            "audio_ms": round(self._fed_ms),
            "live_from_ms": None if self._live_from_ms is None else round(self._live_from_ms),
            "transcript": self._transcript,
            "ack": self._ack,
            "error": None if self._error is None else str(self._error),
            "backchannel_available": self._backchannel_available,
            "backchannel_unavailable_reason": self._backchannel_unavailable_reason,
            "backchannels": [dict(event) for event in self._backchannel_events],
        }

    def start(self) -> None:
        """送信用スレッドを起動する。接続はそのスレッドで行い、その間の音声はためておく。"""
        self._sender = threading.Thread(target=self._send_loop, name="stt-stream-send", daemon=True)
        self._sender.start()

    def feed(self, pcm: bytes, *, live: bool = True) -> None:
        """録音した PCM16 モノラルを送信待ちに積む。

        live=False は、発話の開始より前の音声（ウェイクワードの前後を含む先読み分）を表す。
        その位置で届いた相槌は鳴らさない。
        """
        if not pcm or self._ended or self._closed:
            return
        if live and self._live_from_ms is None:
            self._live_from_ms = self._fed_ms
        self._fed_ms += (len(pcm) // 2) * 1000.0 / self._sample_rate
        self._queue.put(pcm)

    def finish(self, timeout: float = TRANSCRIPT_TIMEOUT_SECONDS) -> str:
        """発話の終わりを送り、文字起こし結果を返す。得られなければ SttStreamError を送出する。"""
        self._end()
        log.info(
            "発話の送信を終えました: 音声=%.1f秒 届いた相槌=%d回",
            self._fed_ms / 1000,
            len(self._backchannel_events),
        )
        if not self._transcript_ready.wait(timeout):
            self.close()
            raise SttStreamError(f"文字起こし結果が{timeout:.0f}秒以内に届きませんでした")
        if self._transcript is None:
            self.close()
            raise SttStreamError(str(self._error or "文字起こし結果を受け取れませんでした"))
        if not self._transcript:
            # 空の文字起こしには ack が来ないため、ここで閉じる。
            self.close()
        return self._transcript

    def wait_ack(self, timeout: float) -> str | None:
        """最初の返事（ack）を待って返す。時間内に来なければ None を返す。いずれの場合も接続を閉じる。"""
        try:
            if self._transcript is None or not self._transcript:
                return None
            if not self._ack_ready.wait(max(0.0, timeout)):
                log.info("stt-gateway の最初の返事が%.1f秒以内に届きませんでした", timeout)
                return None
            return self._ack or None
        finally:
            self.close()

    def close(self) -> None:
        """接続を閉じ、送信と受信のスレッドを終わらせる。何度呼んでもよい。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            ws = self._ws
        self._queue.put(_END)
        self._start_ready.set()
        self._transcript_ready.set()
        self._ack_ready.set()
        if ws is not None:
            try:
                ws.close()
            except Exception:
                log.debug("stt-gateway の接続を閉じられませんでした", exc_info=True)

    def _end(self) -> None:
        """発話の終わりを送信待ちに積む。"""
        if self._ended:
            return
        self._ended = True
        self._queue.put(_END)

    def _fail(self, exc: Exception) -> None:
        """失敗を記録し、結果を待っている呼び出し側を起こす。"""
        if self._error is None:
            self._error = exc
        self._start_ready.set()
        self._transcript_ready.set()
        self._ack_ready.set()

    def _open_connection(self) -> Any:
        """WebSocket で接続する。"""
        connect = self._connect
        if connect is None:
            from websockets.sync.client import connect
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        return connect(self._url, additional_headers=headers, open_timeout=CONNECT_TIMEOUT_SECONDS)

    def _send_loop(self) -> None:
        """接続して start を送り、ためた音声を Opus にして送り、最後に end を送る。"""
        try:
            ws = self._open_connection()
        except Exception as exc:
            log.warning("stt-gateway /stream に接続できません: %s", exc)
            self._fail(SttStreamError(f"stt-gateway /stream に接続できません: {exc}"))
            return
        with self._lock:
            if self._closed:
                ws.close()
                return
            self._ws = ws
        try:
            encoder = self._encoder_factory(self._sample_rate, self._bitrate)
            start = {"type": "start", "encoding": "opus", "language": self._language, **self._start_options}
            ws.send(json.dumps(start, ensure_ascii=False))
            threading.Thread(target=self._receive_loop, args=(ws,), name="stt-stream-recv", daemon=True).start()
            if "phrases_by_kind" in start.get("backchannel", {}):
                # startの受理までは音声をためる。拒否された場合も音声を失わず送り直せる。
                if not self._start_ready.wait(CONNECT_TIMEOUT_SECONDS):
                    raise SttStreamError("startの受理が時間内に届きませんでした")
                if self._closed or self.failed:
                    return
                if self._kind_rejected:
                    start["backchannel"] = dict(start["backchannel"])
                    start["backchannel"].pop("phrases_by_kind")
                    start["backchannel"]["phrases"] = list(self._backchannel_fallback_phrases)
                    self._start_options = {key: value for key, value in start.items() if key != "type"}
                    self._start_ready.clear()
                    ws.send(json.dumps(start, ensure_ascii=False))
                    if not self._start_ready.wait(CONNECT_TIMEOUT_SECONDS):
                        raise SttStreamError("互換指定のstartの受理が時間内に届きませんでした")
                    if self._closed or self.failed:
                        return
            while True:
                item = self._queue.get()
                if item is _END:
                    break
                for packet in encoder.encode(item):  # type: ignore[arg-type]
                    ws.send(packet)
            if self._closed:
                return
            for packet in encoder.flush():
                ws.send(packet)
            ws.send(json.dumps({"type": "end"}))
        except Exception as exc:
            if not self._closed:
                log.warning("stt-gateway /stream への送信に失敗しました: %s", exc)
                self._fail(SttStreamError(f"stt-gateway /stream への送信に失敗しました: {exc}"))
                self.close()

    def _receive_loop(self, ws: Any) -> None:
        """サーバからのイベントを受け取り、種類ごとに処理する。"""
        try:
            for message in ws:
                if isinstance(message, bytes):
                    continue
                try:
                    event = json.loads(message)
                except ValueError:
                    log.warning("stt-gateway から読めないイベントを受け取りました: %s", message[:200])
                    continue
                if isinstance(event, dict):
                    self._handle_event(event)
        except Exception as exc:
            if not self._closed:
                log.warning("stt-gateway /stream の受信が切れました: %s", exc)
        finally:
            if not self._transcript_ready.is_set():
                self._fail(SttStreamError("文字起こし結果を受け取る前に接続が切れました"))
            self._ack_ready.set()

    def _handle_event(self, event: dict[str, Any]) -> None:
        """1 つのイベントを処理する。"""
        kind = event.get("type")
        if kind == "started":
            self._started = True
            self._start_ready.set()
            self._backchannel_available = bool(event.get("backchannel"))
            self._backchannel_unavailable_reason = event.get("backchannel_unavailable_reason")
            if not self._backchannel_available:
                log.info("この発話では相槌を使えません: %s", self._backchannel_unavailable_reason)
        elif kind == "backchannel":
            self._handle_backchannel(event)
        elif kind == "transcript":
            self._transcript = str(event.get("text", "")).strip()
            log.info("ストリーミング文字起こし: %s (latency=%sms)", self._transcript, event.get("latency_ms"))
            self._transcript_ready.set()
            if not self._transcript:
                self._ack_ready.set()
        elif kind == "ack":
            self._ack = str(event.get("text", "")).strip()
            log.info("stt-gateway の最初の返事: %s (source=%s)", self._ack, event.get("source"))
            self._ack_ready.set()
        elif kind == "error":
            code = str(event.get("code", ""))
            detail = str(event.get("detail", ""))
            log.warning("stt-gateway /stream がエラーを返しました: %s %s", code, detail)
            if (
                code == "invalid_message" and not self._started and not self._kind_rejected
                and "phrases_by_kind" in self._start_options.get("backchannel", {})
            ):
                self._kind_rejected = True
                log.warning("分類別の相槌指定を外してstartを再送します。以後の発話も従来の指定を使います")
                if self._on_kind_unsupported is not None:
                    self._on_kind_unsupported()
                self._start_ready.set()
                return
            # started より前の invalid_message は start が受け付けられなかったことを表す。
            # その発話は始まらず transcript も来ないため、時間切れを待たずに失敗にする。
            if code in FATAL_ERROR_CODES or (code == "invalid_message" and not self._started):
                self._fail(SttStreamError(f"stt-gateway /stream エラー {code}: {detail}"))

    def _handle_backchannel(self, event: dict[str, Any]) -> None:
        """相槌のイベントを、録音中で、発話の開始より後の位置なら呼び出し先へ渡す。

        調査のため、届いた相槌はすべて、位置・確率・遅れ・渡したかどうかを記録してログに出す。
        遅れ（lag_ms）は、届いた時点で送り終えていた音声の長さから、相槌の位置を引いたもの。
        録音は実時間で送るため、話した時点から相槌が届くまでの時間の目安になる。
        """
        text = str(event.get("text", "")).strip()
        offset_ms = float(event.get("offset_ms") or 0)
        probability = event.get("probability")
        lag_ms = self._fed_ms - offset_ms
        if not text:
            result = "empty"
        elif self._ended:
            result = "after_end"
        elif self._live_from_ms is None or offset_ms < self._live_from_ms:
            result = "pre_roll"
        elif offset_ms < self._live_from_ms + self._backchannel_start_delay_ms:
            result = "too_early"
        elif self._on_backchannel is None:
            result = "no_handler"
        else:
            result = "delivered"
        self._backchannel_events.append(
            {
                "text": text,
                "offset_ms": round(offset_ms),
                "probability": probability,
                "kind": event.get("kind"),
                "lag_ms": round(lag_ms),
                "result": result,
            }
        )
        log.info(
            "相槌を受信: %s 分類=%s 位置=%.0fms 確率=%s 遅れ=%.0fms 結果=%s",
            text,
            event.get("kind"),
            offset_ms,
            probability,
            lag_ms,
            result,
        )
        if result != "delivered" or self._on_backchannel is None:
            return
        try:
            self._on_backchannel(text)
        except Exception:
            log.exception("相槌の処理に失敗しました")


class SttStreamClient:
    """発話ごとに SttStreamUtterance を作る。"""

    def __init__(
        self,
        base_url: str,
        language: str,
        bearer_token: str = "",
        opus_bitrate: str = "24k",
        connect: Callable[..., Any] | None = None,
        start_options: dict[str, Any] | None = None,
        backchannel_start_delay_ms: float = 0.0,
        backchannel_fallback_phrases: tuple[str, ...] = DEFAULT_STT_BACKCHANNEL_PHRASES,
    ) -> None:
        """stt-gateway のベース URL、言語、Bearer トークン、Opus のビットレート、start に付ける指定、
        話し始めてから相槌を鳴らさない時間を保持する。"""
        self._url = stream_url(base_url)
        self._language = language
        self._token = bearer_token
        self._bitrate = parse_bitrate(opus_bitrate)
        self._connect = connect
        self._start_options = start_options
        self._backchannel_start_delay_ms = backchannel_start_delay_ms
        self._backchannel_fallback_phrases = backchannel_fallback_phrases

    def _disable_kind(self) -> None:
        """古いgatewayでは、次の発話から分類別の指定を外す。"""
        options = dict(self._start_options or {})
        backchannel = dict(options.get("backchannel", {}))
        backchannel.pop("phrases_by_kind", None)
        backchannel["phrases"] = list(self._backchannel_fallback_phrases)
        options["backchannel"] = backchannel
        self._start_options = options

    def open(self, sample_rate: int, on_backchannel: Callable[[str], None] | None = None) -> SttStreamUtterance:
        """新しい発話の送受信を始める。"""
        utterance = SttStreamUtterance(
            url=self._url,
            token=self._token,
            language=self._language,
            sample_rate=sample_rate,
            bitrate=self._bitrate,
            on_backchannel=on_backchannel,
            connect=self._connect,
            start_options=self._start_options,
            backchannel_start_delay_ms=self._backchannel_start_delay_ms,
            on_kind_unsupported=self._disable_kind,
            backchannel_fallback_phrases=self._backchannel_fallback_phrases,
        )
        utterance.start()
        return utterance
