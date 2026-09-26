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
# 継続受付中（応答のあとにマイクが開いている時間）と本人確認中も含める。通知の声がマイクに入って、
# 利用者の発話と間違えて認識されないようにするため。
BUSY_STATUS_CODES = frozenset({"speaking", "listening", "auth_listening", "authenticating", "transcribing", "followup"})
# 完了通知は、会話が終わるまで待つ。エージェントが作業中（考え中）の無音の時間も、会話の途中として扱う。
CONVERSATION_STATUS_CODES = BUSY_STATUS_CODES | {"thinking"}


class NoticeSpeaker:
    """通知を読み上げるスレッド。

    - 対象の通知だけを、短い文にして読み上げる（`build_plan`）。
    - 同じ内容を続けて読み上げない（`min_interval`秒）。
    - 発話中・録音中は待ち（最大 busy_wait_seconds）、ミュート中と、本人確認前（ロック中）の通知は読み上げない。
    - 別スロットの完了通知は、いまの会話が終わるまで待つ（考え中も待つ）。上限（idle_wait_seconds）を
      超えたら、割り込まずに読み上げをやめる（通知欄には残る）。
    - 利用者の発話などで再生を中断されたら、静かになってから読み上げ直す（最大 max_retries 回）。
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
        is_conversation_active: Callable[[], bool] | None = None,
        max_chars: int = 60,
        min_interval: float = 60.0,
        warm_retry_seconds: float = 300.0,
        busy_wait_seconds: float = 30.0,
        idle_wait_seconds: float = 600.0,
        max_retries: int = 2,
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
        # 会話が続いているか。指定がなければ、発話・録音中かどうかと同じ判断にする。
        self._is_conversation_active = is_conversation_active or is_busy
        self._max_chars = max_chars
        self._min_interval = min_interval
        self._warm_retry_seconds = warm_retry_seconds
        self._busy_wait_seconds = busy_wait_seconds
        self._idle_wait_seconds = idle_wait_seconds
        self._max_retries = max(0, max_retries)
        self._retries: dict[str, int] = {}
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

    def _wait_until_quiet(self, plan: SpeechPlan, *, completion: bool, deadline: float) -> bool:
        """読み上げてよい状態になるまで待つ。読み上げてよければTrue、やめるならFalseを返す。

        通常の通知は、発話・録音中ならdeadlineまで待ち、超えたら読み上げる。
        完了通知は、会話が終わるまで待ち、deadlineを超えたら割り込まずにやめる。
        待っている間にミュート・ロック・停止になったら、やめる。
        """
        active = self._is_conversation_active if completion else self._is_busy
        while active() and not self._stop.is_set():
            if self._is_muted() or (self._is_locked() and not plan.is_error):
                return False
            if self._clock() >= deadline:
                if completion:
                    log.info("会話が終わらないため、完了通知の読み上げをやめます: %s", plan.text)
                    return False
                break
            self._sleep(0.2)
        return not (self._stop.is_set() or self._is_muted() or (self._is_locked() and not plan.is_error))

    def _speak(self, plan: SpeechPlan) -> None:
        """1件を読み上げる。声を作れなければ、保存音声、なければ警告音で知らせる。

        利用者の発話などで再生を中断されたら、静かになってから読み上げ直す。
        """
        # 本人確認前は、通知の中身を声に出さない。決まった言葉のエラーだけは、個人情報を含まないので知らせる。
        if self._is_muted() or (self._is_locked() and not plan.is_error):
            return
        completion = plan.key.startswith("response:")
        # 待つ時間の上限は、音声を作る前後の待ちを合わせて数える。
        deadline = self._clock() + (self._idle_wait_seconds if completion else self._busy_wait_seconds)
        if not self._wait_until_quiet(plan, completion=completion, deadline=deadline):
            return
        wav_data = self._prepare_audio(plan)
        # 音声を作っている間に、別の発話や録音が始まった場合も、終わるまで待つ。
        if not self._wait_until_quiet(plan, completion=completion, deadline=deadline):
            return
        if self._play(wav_data, plan.text) is False:
            self._retry_later(plan)
        else:
            self._retries.pop(plan.key, None)

    def _prepare_audio(self, plan: SpeechPlan) -> bytes:
        """再生する音声を用意する。保存済みの声、合成した声、警告音の順に使う。"""
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
        return wav_data if wav_data else self._error_tone()

    def _retry_later(self, plan: SpeechPlan) -> None:
        """再生を中断された通知を、順番待ちへ戻す（最大 max_retries 回）。"""
        attempts = self._retries.get(plan.key, 0)
        if attempts >= self._max_retries:
            self._retries.pop(plan.key, None)
            log.info("再生を中断された通知は、回数の上限に達したため、読み上げ直しません: %s", plan.text)
            return
        self._retries[plan.key] = attempts + 1
        try:
            self._queue.put_nowait(plan)
        except queue.Full:
            self._retries.pop(plan.key, None)

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
