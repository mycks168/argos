"""テキストを音声へ合成して再生する読み上げパイプライン。

ARGOS本体からTTSの配管(tts-filter正規化、VOICEVOX/Kokoroフォールバック、
チャンク分割、キャンセル世代・スロット・ミュートによる中断制御、LCD表示)を
分離する。ArgosApp はこのコントローラへ「状態通知の読み上げ」と
「応答ストリームの読み上げ」を委譲し、録音・認証・監視の調停に専念する。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Iterable

from argos.config import Settings
from argos.core.status_controller import StatusController
from argos.services.dashboard.state import DashboardState
from argos.services.tts.chunker import TextChunker


log = logging.getLogger(__name__)


class SpeechController:
    """TTS合成・再生と、その中断制御・ミュート・LCD表示を担う。"""

    def __init__(
        self,
        *,
        settings: Settings,
        audio,
        lcd,
        tts_filter,
        voicevox,
        kokoro,
        tts_cache,
        dashboard_state: DashboardState,
        status: StatusController,
        voicevox_speakers_by_slot_key: dict[str, int],
        current_slot_key: Callable[[], str],
        is_current_slot: Callable[[str], bool],
        report_error: Callable[[str, Exception], None],
        shutdown: threading.Event,
        muted: bool = False,
    ) -> None:
        """依存する各サービスクライアントとコールバックを保持する。"""
        self._settings = settings
        self._audio = audio
        self._lcd = lcd
        self._tts_filter = tts_filter
        self._voicevox = voicevox
        self._kokoro = kokoro
        self._tts_cache = tts_cache
        self._dashboard_state = dashboard_state
        self._status = status
        self._voicevox_speakers_by_slot_key = voicevox_speakers_by_slot_key
        self._current_slot_key = current_slot_key
        self._is_current_slot = is_current_slot
        self._report_error = report_error
        self._shutdown = shutdown
        self._mute_condition = threading.Condition()
        self._muted = muted
        self._last_tts_finished_at = 0.0
        # バージイン抑止用: 自分がウェイクワードを含むチャンクを読み上げている最中か。
        self._speaking_wakeword = False
        self._wakeword_aliases = tuple(alias.lower() for alias in settings.wakeword_aliases if alias)

    def is_muted(self) -> bool:
        """読み上げミュート中ならTrueを返す。"""
        with self._mute_condition:
            return self._muted

    def set_muted(self, muted: bool) -> bool:
        """ミュート状態を更新し、変化したかを返す。ミュート時は再生中の音声を止める。"""
        with self._mute_condition:
            changed = self._muted != muted
            self._muted = muted
            self._mute_condition.notify_all()
        if muted:
            self._audio.cancel()
        return changed

    def is_speaking_wakeword(self) -> bool:
        """今まさに読み上げているチャンクがウェイクワードを含むか返す。

        バージイン運用でARGOS自身が「アルゴス」等と発話している最中は、
        AECで消し切れない残響が自己検知を起こしうるため割り込みを抑止する。
        """
        return self._speaking_wakeword

    def _chunk_contains_wakeword(self, text: str) -> bool:
        """読み上げテキストにウェイクワード別名が含まれるか判定する。"""
        lowered = text.lower()
        return any(alias in lowered for alias in self._wakeword_aliases)

    def is_tts_cooldown_active(self) -> bool:
        """TTS終了直後の自己音声対策クールダウン中か返す。"""
        cooldown = max(0.0, self._settings.wakeword_tts_cooldown_seconds)
        if cooldown <= 0:
            return False
        return time.monotonic() - self._last_tts_finished_at < cooldown

    def speak_status(self, text: str) -> None:
        """短い状態メッセージを読み上げる。"""
        log.info("状態通知: %s", text)
        self._show_lcd(text)
        if self.is_muted():
            return
        if self._settings.dry_run:
            print(f"ARGOS> {text}")
            return
        try:
            normalized = self._tts_filter.normalize(text)
        except Exception as exc:
            log.exception("状態通知のTTSフィルターに失敗しました")
            self._report_error("TTSフィルター", exc)
            return
        try:
            wav_data = self._synthesize_tts(normalized)
        except Exception:
            log.exception("状態通知のTTSに失敗しました")
            return
        try:
            self._wake_dashboard_display()
            self._audio.play_wav(wav_data)
        except Exception as exc:
            log.exception("状態通知の音声再生に失敗しました")
            self._report_error("音声再生", exc)

    def speak_response_stream(self, deltas: Iterable[str], dashboard_message_id: str = "", slot_key: str = "") -> str:
        """応答差分を句読点で分割し、TTS へ順次投入する。"""
        full_response = ""
        chunker = TextChunker(self._settings.tts_delimiters)
        if self._settings.dry_run:
            if not self.is_muted():
                print("ARGOS> ", end="", flush=True)
            for delta in deltas:
                full_response += delta
                if dashboard_message_id:
                    self._dashboard_state.append_message(dashboard_message_id, delta)
                if not self.is_muted():
                    print(delta, end="", flush=True)
            if not self.is_muted():
                print()
            return full_response

        stream_generation = self._current_generation()
        tts_queue: queue.Queue[str | None] = queue.Queue()
        worker = threading.Thread(target=self._tts_worker, args=(tts_queue, stream_generation, slot_key), daemon=True)
        worker.start()

        for delta in deltas:
            full_response += delta
            if dashboard_message_id:
                self._dashboard_state.append_message(dashboard_message_id, delta)
            log.info("エージェント応答差分: %s", delta[:120])
            for chunk in chunker.push(delta):
                if self._current_generation() != stream_generation:
                    log.info("キャンセル済みのため TTS チャンクを読み上げません: %s", chunk[:80])
                    continue
                if slot_key and not self._is_current_slot(slot_key):
                    log.info("非表示スロットのため TTS チャンクを読み上げません: %s", chunk[:80])
                    continue
                log.info("TTS チャンク投入: %s", chunk[:80])
                tts_queue.put(chunk)

        rest = chunker.flush()
        if rest and self._current_generation() == stream_generation and (not slot_key or self._is_current_slot(slot_key)):
            log.info("TTS 最終チャンク投入: %s", rest[:80])
            tts_queue.put(rest)
        tts_queue.put(None)
        worker.join(timeout=300)
        return full_response

    def _tts_worker(self, tts_queue: queue.Queue[str | None], generation: int, slot_key: str = "") -> None:
        """TTS チャンクを順に合成して再生する。"""
        while True:
            chunk = tts_queue.get()
            if chunk is None:
                return
            if self._current_generation() != generation:
                self._drain_tts_queue(tts_queue)
                return
            if slot_key and not self._is_current_slot(slot_key):
                self._drain_tts_queue(tts_queue)
                return
            if not self._wait_until_unmuted(generation):
                self._drain_tts_queue(tts_queue)
                return
            self._show_lcd(chunk)
            self._status.set(generation, "speaking", "読み上げ中")
            try:
                normalized = self._tts_filter.normalize(chunk)
            except Exception as exc:
                log.exception("TTSフィルターに失敗しました")
                self._report_error("TTSフィルター", exc)
                self._drain_tts_queue(tts_queue)
                return
            if self._current_generation() != generation:
                self._drain_tts_queue(tts_queue)
                return
            try:
                wav_data = self._synthesize_tts(normalized, slot_key)
            except Exception:
                log.exception("TTSに失敗しました")
                self._drain_tts_queue(tts_queue)
                return
            if self._current_generation() != generation:
                self._drain_tts_queue(tts_queue)
                return
            if not self._wait_until_unmuted(generation):
                self._drain_tts_queue(tts_queue)
                return
            try:
                self._wake_dashboard_display()
                self._speaking_wakeword = self._chunk_contains_wakeword(normalized)
                self._audio.play_wav(wav_data)
                self._mark_tts_finished()
            except Exception as exc:
                log.exception("音声再生に失敗しました")
                self._report_error("音声再生", exc)
                self._drain_tts_queue(tts_queue)
                return
            finally:
                self._speaking_wakeword = False
            if self._current_generation() != generation:
                self._drain_tts_queue(tts_queue)
                return

    def _drain_tts_queue(self, tts_queue: queue.Queue[str | None]) -> None:
        """キャンセル済みの TTS キューに残ったチャンクを破棄する。"""
        while True:
            try:
                tts_queue.get_nowait()
            except queue.Empty:
                return

    def _synthesize_tts(self, text: str, slot_key: str = "") -> bytes:
        """VOICEVOXを優先し、未設定または失敗時はKokoroで音声を生成する。"""
        speaker_id = self._voicevox_speaker_for_slot(slot_key)
        if self._settings.tts_cache_enabled:
            cached = self._tts_cache.get(text, speaker_id)
            if cached is not None:
                return cached

        if self._settings.voicevox_url.strip():
            try:
                wav_data = self._voicevox.synthesize(text, speaker=speaker_id)
                if self._settings.tts_cache_enabled:
                    self._tts_cache.set(text, speaker_id, wav_data)
                return wav_data
            except Exception as exc:
                log.exception("VOICEVOXに失敗しました。Kokoroへフォールバックします")
                self._report_error("VOICEVOX", exc)
        try:
            wav_data = self._kokoro.synthesize(text)
            if self._settings.tts_cache_enabled:
                self._tts_cache.set(text, speaker_id, wav_data)
            return wav_data
        except Exception as exc:
            log.exception("Kokoroに失敗しました")
            self._report_error("Kokoro", exc)
            raise

    def _voicevox_speaker_for_slot(self, slot_key: str = "") -> int:
        """指定スロットまたは現在スロットのVOICEVOX話者IDを返す。"""
        key = slot_key or self._current_slot_key()
        return self._voicevox_speakers_by_slot_key.get(key, self._settings.voicevox_speaker)

    def _wait_until_unmuted(self, generation: int) -> bool:
        """ミュート解除またはキャンセルまでTTSワーカーを待機させる。"""
        with self._mute_condition:
            while self._muted and self._current_generation() == generation and not self._shutdown.is_set():
                self._mute_condition.wait(timeout=0.2)
        return self._current_generation() == generation and not self._shutdown.is_set()

    def _wake_dashboard_display(self) -> None:
        """読み上げ開始に合わせてダッシュボードのスクリーンセーバーを解除する。"""
        self._dashboard_state.wake_display()

    def _mark_tts_finished(self) -> None:
        """ウェイクワード自己検知を避けるためTTS終了時刻を記録する。"""
        self._last_tts_finished_at = time.monotonic()

    def _show_lcd(self, text: str) -> None:
        """LCDが有効ならテキストを表示する。"""
        if self._lcd is None:
            return
        try:
            self._lcd.show_text(text)
        except Exception:
            log.exception("LCD表示に失敗しました")

    def _current_generation(self) -> int:
        """現在の対話世代トークンを返す。"""
        return self._status.current_generation()
