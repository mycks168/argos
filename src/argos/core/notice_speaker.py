"""通知の読み上げを、順番に、ほかの発話と重ならないように行う。"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

from argos.services.notice_speech import ERROR_PHRASES, PhraseStore, SpeechPlan, build_error_tone, build_plan, is_valid_wav

log = logging.getLogger(__name__)

# 発話や録音の最中は、声が重なったり録音に入ったりしないよう、終わるまで待つ状態。
BUSY_STATUS_CODES = frozenset({"speaking", "listening", "auth_listening", "transcribing"})


class NoticeSpeaker:
    """通知を読み上げるスレッド。

    - 対象の通知だけを、短い文にして読み上げる（`build_plan`）。
    - 同じ内容を続けて読み上げない（`min_interval`秒）。
    - 発話中・録音中は待ち、ミュート中と、本人確認前（ロック中）の通知は読み上げない。
    - 音声合成が使えないときは、エラーは保存済みの音声、なければ警告音で知らせる。
    - 音声合成が使えるうちに、エラーの決まった言葉を保存しておく（`warm`）。

    音声の生成・再生・状態の確認は外から渡すので、実機なしで動作を検証できる。
    """

    def __init__(
        self,
        *,
        enabled: bool,
        synthesize: Callable[[str, int], bytes],
        play: Callable[[bytes], None],
        store: PhraseStore,
        speakers: Callable[[], Iterable[int]],
        current_speaker: Callable[[], int],
        is_muted: Callable[[], bool],
        is_busy: Callable[[], bool],
        is_locked: Callable[[], bool] = lambda: False,
        max_chars: int = 60,
        min_interval: float = 60.0,
        warm_retry_seconds: float = 300.0,
        busy_wait_seconds: float = 30.0,
        max_queue: int = 5,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """読み上げに使う処理と、待ち時間などの設定を保持する。"""
        self._enabled = enabled
        self._synthesize = synthesize
        self._play = play
        self._store = store
        self._speakers = speakers
        self._current_speaker = current_speaker
        self._is_muted = is_muted
        self._is_busy = is_busy
        self._is_locked = is_locked
        self._max_chars = max_chars
        self._min_interval = min_interval
        self._warm_retry_seconds = warm_retry_seconds
        self._busy_wait_seconds = busy_wait_seconds
        self._clock = clock
        self._sleep = sleep
        self._queue: queue.Queue[SpeechPlan] = queue.Queue(maxsize=max(1, max_queue))
        self._last_spoken: dict[str, float] = {}
        self._next_warm_at = 0.0
        self._warmed = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tone: bytes | None = None

    @property
    def enabled(self) -> bool:
        """通知の読み上げが有効ならTrueを返す。"""
        return self._enabled

    def start(self) -> None:
        """読み上げスレッドを開始する。無効な設定では何もしない。"""
        if not self._enabled or (self._thread is not None and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="notice-speaker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """読み上げスレッドを止める。"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def submit(self, notice: dict[str, Any]) -> bool:
        """通知を読み上げの順番待ちに入れる。受け付けたらTrueを返す。

        対象外の通知、直近に同じ内容を読み上げた通知は受け付けない。順番待ちがいっぱいなら、
        一番古いものを捨てて、新しいものを優先する。
        """
        if not self._enabled:
            return False
        plan = build_plan(notice, self._max_chars)
        if plan is None or self._is_throttled(plan):
            return False
        # 受け付けた時点で「読み上げ予定」として記録し、連続する同じ通知が並ばないようにする。
        self._last_spoken[plan.key] = self._clock()
        while True:
            try:
                self._queue.put_nowait(plan)
                return True
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass

    def _is_throttled(self, plan: SpeechPlan) -> bool:
        """同じ内容を、最小間隔以内にもう一度読み上げようとしているか判定する。"""
        last = self._last_spoken.get(plan.key)
        return last is not None and self._clock() - last < self._min_interval

    def _run(self) -> None:
        """順番待ちの通知を読み上げ、空いている間はエラーの音声を保存する。"""
        while not self._stop.is_set():
            try:
                plan = self._queue.get(timeout=0.5)
            except queue.Empty:
                self.warm()
                continue
            try:
                self._speak(plan)
            except Exception:  # noqa: BLE001 - 読み上げの失敗で、通知の処理全体を止めない
                log.exception("通知の読み上げに失敗しました")

    def _wait_until_quiet(self) -> None:
        """発話中・録音中なら、終わるまで最大 busy_wait_seconds 待つ。"""
        deadline = self._clock() + self._busy_wait_seconds
        while self._is_busy() and self._clock() < deadline and not self._stop.is_set():
            self._sleep(0.2)

    def _speak(self, plan: SpeechPlan) -> None:
        """1件を読み上げる。声を作れなければ、保存音声、なければ警告音で知らせる。"""
        # 本人確認前は、通知の中身を声に出さない。決まった言葉のエラーだけは、個人情報を含まないので知らせる。
        if self._is_muted() or (self._is_locked() and not plan.is_error):
            return
        self._wait_until_quiet()
        if self._is_muted() or self._stop.is_set():
            return
        speaker = self._current_speaker()
        wav_data: bytes | None = None
        if plan.is_error:
            wav_data = self._store.get(plan.text, speaker)
        if wav_data is None:
            try:
                wav_data = self._synthesize(plan.text, speaker)
                if not is_valid_wav(wav_data):
                    raise RuntimeError("再生できない音声が返されました")
            except Exception as exc:  # noqa: BLE001 - 音声合成の失敗は、警告音に切り替えて続ける
                log.warning("通知の音声合成に失敗したため、警告音で知らせます: %s", exc)
                wav_data = None
            else:
                if plan.is_error:
                    self._store.put(plan.text, speaker, wav_data)
        self._play(wav_data if wav_data else self._error_tone())

    def _error_tone(self) -> bytes:
        """警告音を返す。初回だけ生成する。"""
        if self._tone is None:
            self._tone = build_error_tone()
        return self._tone

    def warm(self) -> bool:
        """エラーの決まった言葉を、話者ごとに保存する。すべて保存済みならTrueを返す。

        音声合成が壊れる前に用意しておくためのもの。合成できないときは、次の機会
        （warm_retry_seconds後）に再挑戦する。
        """
        if self._warmed or not self._enabled:
            return self._warmed
        now = self._clock()
        if now < self._next_warm_at:
            return False
        phrases = sorted(set(ERROR_PHRASES.values()))
        missing = [(text, speaker) for speaker in set(self._speakers()) for text in phrases if not self._store.has(text, speaker)]
        try:
            for text, speaker in missing:
                if self._stop.is_set():
                    return False
                if not self._store.put(text, speaker, self._synthesize(text, speaker)):
                    raise RuntimeError("再生できない音声が返されました")
        except Exception as exc:  # noqa: BLE001 - 音声合成が使えない間は、あとで再挑戦する
            log.info("エラーの音声の保存は、音声合成が使えないため見送ります: %s", exc)
            self._next_warm_at = now + self._warm_retry_seconds
            return False
        self._warmed = True
        return True
