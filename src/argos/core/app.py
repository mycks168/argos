"""ARGOS のメイン制御。"""

from __future__ import annotations

import logging
import queue
import random
import signal
import threading
import time
from collections.abc import Iterable

from argos.config import Settings
from argos.hardware.audio import AudioPlayer, Recorder, check_audio_level
from argos.hardware.button import ButtonPtt
from argos.hardware.gpio import GpioPttInput
from argos.hardware.lcd import St7789TextDisplay
from argos.services.codex.cli import CodexCliClient
from argos.services.dashboard.server import DashboardServer
from argos.services.dashboard.state import DashboardState
from argos.services.stt.gateway import SttGatewayClient
from argos.services.tts.chunker import TextChunker
from argos.services.tts.filter import TtsFilterClient
from argos.services.tts.voicevox import VoicevoxClient


log = logging.getLogger(__name__)


CODEX_PROGRESS_START_PHRASES = (
    "わかった。少し待ってね。",
    "了解。やってみるね。",
    "確認するね。",
    "ちょっと待ってて。",
    "今見てみるね。",
    "すぐ調べるね。",
)

CODEX_PROGRESS_WAIT_PHRASES = (
    "ちょっと時間かかってるけど、もう少し待ってね。",
    "もう少しだけ待ってね。まだ確認中だよ。",
    "まだ確認してる途中だよ。少し待ってね。",
    "時間かかってるけど、もうちょっと待ってね。",
)


class CodexProgressAnnouncer:
    """Codex 待機中の進捗音声を管理する。"""

    def __init__(
        self,
        speak_status,
        first_delay_seconds: float,
        interval_seconds: float,
    ) -> None:
        """読み上げ関数と通知間隔を初期化する。"""
        self._speak_status = speak_status
        self._first_delay_seconds = first_delay_seconds
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """開始メッセージを読み上げ、待機通知スレッドを起動する。"""
        self._speak_random(CODEX_PROGRESS_START_PHRASES)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """待機通知を停止する。"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _run(self) -> None:
        """一定時間ごとに待機中メッセージを読み上げる。"""
        if self._stop.wait(self._first_delay_seconds):
            return
        while not self._stop.is_set():
            self._speak_random(CODEX_PROGRESS_WAIT_PHRASES)
            if self._stop.wait(self._interval_seconds):
                return

    def _speak_random(self, phrases: tuple[str, ...]) -> None:
        """候補からランダムに1つ読み上げる。"""
        self._speak_status(random.choice(phrases))


class ArgosApp:
    """PTT 録音から Codex 応答の読み上げまでを束ねる。"""

    def __init__(self, settings: Settings) -> None:
        """各サービスクライアントと状態機械を初期化する。"""
        self._settings = settings
        self._recorder = Recorder(settings.audio_input_device, settings.audio_sample_rate)
        self._stt = SttGatewayClient(settings.stt_gateway_url, settings.stt_language)
        self._codex = CodexCliClient(settings)
        self._tts_filter = TtsFilterClient(settings.tts_filter_url, settings.tts_filter_token)
        self._voicevox = VoicevoxClient(settings.voicevox_url, settings.voicevox_speaker, settings.voicevox_sample_rate)
        self._audio = AudioPlayer(settings.audio_output_device, settings.audio_output_card, settings.audio_output_volume)
        self._lcd = self._create_lcd_display(settings)
        self._dashboard_state = DashboardState()
        self._dashboard_server = self._create_dashboard_server(settings)
        self._button = ButtonPtt(
            on_press=self._on_ptt_press,
            on_release=self._on_ptt_release,
            on_double_click=self._on_double_click,
            on_cancel=self._on_cancel,
        )
        self._shutdown = threading.Event()
        self._cancel_lock = threading.Lock()
        self._cancel_generation = 0
        self._worker: threading.Thread | None = None
        self._gpio: GpioPttInput | None = None

    def _create_lcd_display(self, settings: Settings) -> St7789TextDisplay | None:
        """設定に応じてLCD表示器を初期化する。"""
        if not settings.lcd_enabled:
            return None
        try:
            return St7789TextDisplay.create(settings)
        except Exception:
            log.exception("LCD表示を初期化できないため無効化します")
            return None

    def _create_dashboard_server(self, settings: Settings) -> DashboardServer | None:
        """設定に応じてHDMIダッシュボードサーバーを作成する。"""
        if not settings.dashboard_enabled:
            return None
        return DashboardServer(
            state=self._dashboard_state,
            host=settings.dashboard_host,
            port=settings.dashboard_port,
            token=settings.dashboard_token,
        )

    def run(self) -> None:
        """ARGOS を起動し、終了シグナルまで待機する。"""
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)
        log.info("ARGOS 起動: 現在の Codex スロット=%s", self._codex.current_name)
        if self._dashboard_server is not None:
            self._dashboard_server.start()
        self._dashboard_state.set_status("ready", "待機中")
        if self._settings.dry_run:
            self._run_text_loop()
            return
        self._gpio = GpioPttInput(self._settings.ptt_gpio, self._button.handle_press, self._button.handle_release)
        while not self._shutdown.is_set():
            time.sleep(0.2)

    def _run_text_loop(self) -> None:
        """DRY_RUN 用に標準入力から発話テキストを受け付ける。"""
        print("ARGOS DRY_RUN: テキストを入力してください。空行で終了、/next でスロット切替、/reset で現スロット初期化。")
        while not self._shutdown.is_set():
            try:
                text = input("> ").strip()
            except EOFError:
                break
            if not text:
                break
            if text == "/next":
                self._speak_status(f"{self._codex.next_slot()}に切り替えました")
                continue
            if text == "/reset":
                self._codex.reset_current()
                self._speak_status("現在のセッションを新規会話にしました")
                continue
            self._handle_text(text)

    def _on_ptt_press(self) -> None:
        """PTT 押下時に録音を開始する。"""
        log.info("PTT ON: 録音開始")
        self._dashboard_state.set_status("listening", "録音中")
        self._cancel_active_audio()
        self._recorder.start()

    def _on_ptt_release(self) -> None:
        """PTT 解放時に録音を停止し、処理スレッドを開始する。"""
        log.info("PTT OFF: 録音停止と処理開始")
        self._dashboard_state.set_status("thinking", "文字起こし中")
        self._worker = threading.Thread(target=self._process_recording, daemon=True)
        self._worker.start()

    def _on_double_click(self) -> None:
        """ダブルクリックで Codex スロットを切り替える。"""
        name = self._codex.next_slot()
        log.info("Codex スロット切替: %s", name)
        self._speak_status(f"{name}に切り替えました")

    def _on_cancel(self) -> None:
        """処理中の音声入出力をキャンセルする。"""
        log.info("キャンセル要求: 録音破棄と再生停止")
        self._recorder.cancel()
        self._cancel_active_audio()

    def _cancel_active_audio(self) -> int:
        """再生中と未再生チャンクを無効化し、キャンセル世代を返す。"""
        with self._cancel_lock:
            self._cancel_generation += 1
            generation = self._cancel_generation
        self._audio.cancel()
        return generation

    def _current_cancel_generation(self) -> int:
        """現在のキャンセル世代を返す。"""
        with self._cancel_lock:
            return self._cancel_generation

    def _process_recording(self) -> None:
        """録音済み WAV を STT、Codex、TTS の順に処理する。"""
        try:
            wav_path = self._recorder.stop()
            level = check_audio_level(wav_path)
            if level < self._settings.silence_rms_threshold:
                log.info("無音として破棄しました: RMS=%.1f", level)
                return
            transcript = self._stt.transcribe(wav_path)
            if not transcript:
                return
            self._handle_text(transcript)
        except Exception as exc:
            log.exception("音声処理に失敗しました")
            self._dashboard_state.set_status("error", "処理エラー")
            self._speak_status(f"処理に失敗しました。{exc}")
        finally:
            self._button.mark_idle()
            self._dashboard_state.set_status("ready", "待機中")

    def _handle_text(self, text: str) -> None:
        """テキストを Codex に送り、応答を読み上げる。"""
        log.info("ユーザ発話: %s", text)
        self._dashboard_state.add_message("user", text)
        self._dashboard_state.set_status("thinking", "考え中")
        announcer = self._start_codex_progress()
        dashboard_message_id = self._dashboard_state.add_message("assistant", "", streaming=True)
        try:
            response = self._speak_response_stream(
                self._stop_progress_on_first_delta(self._codex.ask_stream(text), announcer),
                dashboard_message_id=dashboard_message_id,
            )
            log.info("Codex 応答: %s", response[:300])
        finally:
            self._dashboard_state.finish_message(dashboard_message_id)
            if announcer is not None:
                announcer.stop()

    def _speak_response(self, text: str) -> None:
        """Codex 応答を tts-filter と VOICEVOX に通して再生する。"""
        if not text:
            return
        if self._settings.dry_run:
            print(f"ARGOS> {text}")
            return
        self._show_lcd(text)
        normalized = self._tts_filter.normalize(text)
        wav_data = self._voicevox.synthesize(normalized)
        self._audio.play_wav(wav_data)

    def _speak_response_stream(self, deltas: Iterable[str], dashboard_message_id: str = "") -> str:
        """応答差分を句読点で分割し、VOICEVOX へ順次投入する。"""
        full_response = ""
        chunker = TextChunker(self._settings.tts_delimiters)
        if self._settings.dry_run:
            print("ARGOS> ", end="", flush=True)
            for delta in deltas:
                full_response += delta
                if dashboard_message_id:
                    self._dashboard_state.append_message(dashboard_message_id, delta)
                print(delta, end="", flush=True)
            print()
            return full_response

        stream_generation = self._current_cancel_generation()
        tts_queue: queue.Queue[str | None] = queue.Queue()
        worker = threading.Thread(target=self._tts_worker, args=(tts_queue, stream_generation), daemon=True)
        worker.start()

        for delta in deltas:
            if self._current_cancel_generation() != stream_generation:
                log.info("キャンセル済みのため Codex 応答読み上げを中断します")
                break
            full_response += delta
            if dashboard_message_id:
                self._dashboard_state.append_message(dashboard_message_id, delta)
            log.info("Codex 応答差分: %s", delta[:120])
            for chunk in chunker.push(delta):
                if self._current_cancel_generation() != stream_generation:
                    log.info("キャンセル済みのため VOICEVOX チャンク投入を停止します")
                    break
                log.info("VOICEVOX チャンク投入: %s", chunk[:80])
                tts_queue.put(chunk)

        rest = chunker.flush()
        if rest and self._current_cancel_generation() == stream_generation:
            log.info("VOICEVOX 最終チャンク投入: %s", rest[:80])
            tts_queue.put(rest)
        tts_queue.put(None)
        worker.join(timeout=300)
        return full_response

    def _start_codex_progress(self) -> CodexProgressAnnouncer | None:
        """設定に応じてCodex待機中の進捗音声を開始する。"""
        if not self._settings.codex_progress_voice:
            return None
        announcer = CodexProgressAnnouncer(
            speak_status=self._speak_status,
            first_delay_seconds=self._settings.codex_progress_first_delay_seconds,
            interval_seconds=self._settings.codex_progress_interval_seconds,
        )
        announcer.start()
        return announcer

    def _stop_progress_on_first_delta(
        self,
        deltas: Iterable[str],
        announcer: CodexProgressAnnouncer | None,
    ) -> Iterable[str]:
        """Codex本文が届いた時点で進捗音声を止める。"""
        stopped = False
        for delta in deltas:
            if not stopped and announcer is not None:
                announcer.stop()
                stopped = True
            yield delta

    def _tts_worker(self, tts_queue: queue.Queue[str | None], generation: int) -> None:
        """VOICEVOX チャンクを順に合成して再生する。"""
        while True:
            chunk = tts_queue.get()
            if chunk is None:
                return
            if self._current_cancel_generation() != generation:
                self._drain_tts_queue(tts_queue)
                return
            self._show_lcd(chunk)
            self._dashboard_state.set_status("speaking", "読み上げ中")
            normalized = self._tts_filter.normalize(chunk)
            if self._current_cancel_generation() != generation:
                self._drain_tts_queue(tts_queue)
                return
            wav_data = self._voicevox.synthesize(normalized)
            if self._current_cancel_generation() != generation:
                self._drain_tts_queue(tts_queue)
                return
            self._audio.play_wav(wav_data)
            if self._current_cancel_generation() != generation:
                self._drain_tts_queue(tts_queue)
                return

    def _drain_tts_queue(self, tts_queue: queue.Queue[str | None]) -> None:
        """キャンセル済みの TTS キューに残ったチャンクを破棄する。"""
        while True:
            try:
                tts_queue.get_nowait()
            except queue.Empty:
                return

    def _speak_status(self, text: str) -> None:
        """短い状態メッセージを読み上げる。"""
        log.info("状態通知: %s", text)
        self._show_lcd(text)
        if self._settings.dry_run:
            print(f"ARGOS> {text}")
            return
        try:
            normalized = self._tts_filter.normalize(text)
            self._audio.play_wav(self._voicevox.synthesize(normalized))
        except Exception:
            log.exception("状態通知の読み上げに失敗しました")

    def _show_lcd(self, text: str) -> None:
        """LCDが有効ならテキストを表示する。"""
        if self._lcd is None:
            return
        try:
            self._lcd.show_text(text)
        except Exception:
            log.exception("LCD表示に失敗しました")

    def _handle_signal(self, signum: int, _frame: object) -> None:
        """終了シグナルを受けて停止する。"""
        log.info("終了シグナルを受信しました: %s", signum)
        self._shutdown.set()
        self._recorder.cancel()
        self._cancel_active_audio()
        if self._dashboard_server is not None:
            self._dashboard_server.stop()
