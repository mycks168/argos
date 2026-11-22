"""stt-gateway の WebSocket /stream クライアント。

録音しながら音声を Opus の生パケットにして送り、話している最中の相槌（backchannel）、
文字起こし結果（transcript）、最初の返事（ack）を受け取る。1 つの発話ごとに
1 本の接続を張り、発話が終わって ack を受け取ったら閉じる。
"""

from __future__ import annotations

import json
import logging
import queue
import random
import re
import threading
from collections.abc import Callable
from typing import Any

import numpy as np


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
# 一方で中身のない「つなぎ」の一言だけにすると、gemma4:e4b では「なるほどね。」に偏り
# （206件中84件）、これから取りかかる感じが出なかった。そこで、後で別の担当が答えることを伝え、
# 話の種類（質問・作業の依頼・困りごと・相談や雑談・あいさつ）ごとに、取りかかる言い方
# （調べる・やってみる・見てみる・考える）を選ばせる（stt-gateway 側の検証 2026-11-22）。
# 実際の作業はエージェントが行い、断ったり失敗したりすることもあるため、作業の中身は言わせない。
# 小型の LLM は禁止として書いた言葉に引っ張られる（2026-11-15）ので、禁止語は並べず、
# 使う言い方を示す形で指示する。
# 書き出し（「じゃあ、」など）はモデルに作らせない。出力1トークンは入力の約75トークン分重く、
# 書き出しの約3トークンで約25ms遅くなるうえ、リクエストごとに独立しているため「毎回変える」指示が
# 効かず「じゃあ」に偏った（206件中122件）。書き出しは ARGOS 側で付ける（add_ack_opener）。
# 例は種類ごとに1組。「やってみるね」の例が2組あると、質問にも「やってみるね」と返しがちだった
# （質問94件中38件）。質問の例は聞き方を変えて入れる。
# 「聞き手」「答えを知らない」「日本語で」を外すと、小型の LLM が質問に答えて言い切ったり、
# 日本語以外で返したりする（stt-gateway の変更履歴 2026-11-15）。
ARGOS_ACK_OPTIONS: dict[str, Any] = {
    "system_prompt": (
        "あなたは会話の聞き手です。あなたの返事の後に、別の担当が相手にきちんと答えます。"
        "あなたは答えを知らないので、質問や依頼には答えず、これから取りかかることだけを一言で伝えます。"
        "言い方は話の種類で選びます。質問（どう？・なに？・どうやるの？など）なら「調べるね。」、"
        "作業の依頼なら「やってみるね。」、困りごとや不具合なら「見てみるね。」、"
        "相談や雑談なら「考えるね。」。あいさつにだけは、あいさつで返します。"
        "何をするかの中身は言わず、この4つの言い方だけで返します。"
        "相手の言葉は繰り返しません。聞き返しもしません。"
        "日本語で、10文字以内、くだけた口調、1文だけ。"
    ),
    "examples": [
        {"user": "拡大表示ってどうやって実装してるの", "reply": "調べるね。"},
        {"user": "近くのガソリンスタンド探してくれる", "reply": "やってみるね。"},
        {"user": "なんか画面が固まってるんだけど", "reply": "見てみるね。"},
        {"user": "ミニゲームとか作ったら面白いかな", "reply": "考えるね。"},
        {"user": "お疲れさま、今日はもういいよ", "reply": "お疲れさま！"},
    ],
    "fallback_text": "えっと、ちょっと待ってね。",
}

# 最初の返事の前に ARGOS が付ける書き出し。空文字は「付けない」。直前と同じものは選ばない。
ACK_OPENERS = ("えっと、", "じゃあ、", "うん、", "よし、", "")
# 書き出しを付けるのは、取りかかる言い方で終わる短い返事だけ。あいさつや、gateway の
# 固定の文言（すでに書き出しがある）には付けない。
_ACK_ACTION_PATTERN = re.compile(r"^(?:ちょっと)?(?:調べ|やって|見て|考え|探し|確認し)(?:て?み)?る?ね[。！]?$")
_ACK_OPENER_STARTS = tuple(opener for opener in ACK_OPENERS if opener) + ("えーっと", "うーん", "そっか", "なるほど")
_last_opener: str | None = None
_opener_lock = threading.Lock()


def add_ack_opener(text: str, choice: Callable[[list[str]], str] = random.choice) -> str:
    """取りかかる言い方の返事に、直前とは違う書き出しを付けて返す。対象外の返事はそのまま返す。"""
    global _last_opener
    stripped = text.strip()
    if not _ACK_ACTION_PATTERN.match(stripped) or stripped.startswith(_ACK_OPENER_STARTS):
        return stripped
    with _opener_lock:
        opener = choice([candidate for candidate in ACK_OPENERS if candidate != _last_opener])
        _last_opener = opener
    return opener + stripped


def build_start_options(
    *,
    ack_enabled: bool,
    backchannel_phrases: tuple[str, ...],
    backchannel_min_interval_ms: int,
    backchannel_silence_ms: int = 0,
) -> dict[str, Any]:
    """start に付ける、発話ごとの ack と backchannel の指定を作る。

    相槌のタイミングは stt-gateway が無音の長さと区切りの判定で決めるため、ARGOS が送るのは
    文言（phrases）・相槌の間隔（min_interval_ms）・無音の長さ（silence_ms）だけ。
    silence_ms は 0 のとき項目ごと省き、stt-gateway の既定に任せる。

    ack を読み上げない設定（進捗音声がオフ）のときは "ack": false にして、
    stt-gateway に LLM で返事を作らせない。
    """
    backchannel: dict[str, Any] = {
        "min_interval_ms": backchannel_min_interval_ms,
    }
    if backchannel_silence_ms > 0:
        backchannel["silence_ms"] = backchannel_silence_ms
    if backchannel_phrases:
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
            # 書き出しはモデルに作らせず、ここで付ける（ARGOS_ACK_OPTIONS の説明を参照）。
            return add_ack_opener(self._ack) if self._ack else None
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
                "lag_ms": round(lag_ms),
                "result": result,
            }
        )
        log.info(
            "相槌を受信: %s 位置=%.0fms 確率=%s 遅れ=%.0fms 結果=%s",
            text,
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
        )
        utterance.start()
        return utterance
