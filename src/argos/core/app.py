"""ARGOS のメイン制御。"""

from __future__ import annotations

import logging
import queue
import random
import shutil
import signal
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from argos.config import Settings
from argos.hardware.audio import AudioPlayer, Recorder, check_audio_level
from argos.hardware.button import ButtonPtt
from argos.hardware.gpio import GpioPttInput
from argos.hardware.lcd import St7789TextDisplay
from argos.services.auth import AuthGate
from argos.services.codex.cli import CodexCliClient
from argos.services.dashboard.server import DashboardServer
from argos.services.dashboard.state import DashboardState
from argos.services.face_auth import FaceAuthVerifier
from argos.services.greeting import GreetingManager
from argos.services.security_alert import SecurityAlertDispatcher
from argos.services.startup import build_auth_warning_tone, build_startup_chime
from argos.services.stt.gateway import SttGatewayClient
from argos.services.stt.whisper import FasterWhisperClient
from argos.services.tts.chunker import TextChunker
from argos.services.tts.filter import TtsFilterClient
from argos.services.tts.kokoro import KokoroClient
from argos.services.tts.voicevox import VoicevoxClient


log = logging.getLogger(__name__)
FACE_AUTH_FAILURE_IMAGE_PATH = Path("/tmp/argos/camera-latest.jpg")
FACE_AUTH_FAILURE_IMAGE_URL = "/camera/latest.jpg"


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
        self._stt = SttGatewayClient(settings.stt_gateway_url, settings.stt_language, settings.stt_gateway_token)
        self._local_stt = FasterWhisperClient(
            settings.whisper_model_size,
            settings.stt_language,
            settings.whisper_device,
            settings.whisper_compute_type,
        )
        self._codex = CodexCliClient(settings)
        self._tts_filter = TtsFilterClient(settings.tts_filter_url, settings.tts_filter_token)
        self._voicevox = VoicevoxClient(
            settings.voicevox_url,
            settings.voicevox_speaker,
            settings.voicevox_sample_rate,
            settings.voicevox_speed_scale,
        )
        self._kokoro = KokoroClient(
            settings.kokoro_voice,
            settings.kokoro_speed,
            settings.kokoro_repo_id,
            settings.kokoro_sample_rate,
        )
        self._audio = AudioPlayer(settings.audio_output_device, settings.audio_output_card, settings.audio_output_volume)
        self._lcd = self._create_lcd_display(settings)
        self._dashboard_state = DashboardState()
        self._dashboard_server = self._create_dashboard_server(settings)
        self._greeting = GreetingManager(settings.greeting_state_path) if settings.greeting_enabled else None
        self._auth = AuthGate(
            settings.auth_enabled,
            settings.auth_keyword_hash,
            settings.auth_trust_seconds,
            settings.auth_failure_threshold,
        )
        self._face_auth = FaceAuthVerifier(
            settings.auth_face_enabled,
            settings.auth_face_samples_dir,
            settings.auth_face_capture_command,
            settings.auth_face_capture_path,
            settings.auth_face_threshold,
            settings.auth_face_min_matches,
            settings.auth_face_detection_enabled,
            settings.auth_face_min_detected_faces,
            settings.auth_face_max_detected_faces,
            settings.auth_face_image_rotation,
            settings.auth_face_detector_model_path,
            settings.auth_face_recognizer_model_path,
            settings.auth_face_sface_threshold,
        )
        self._security_alert = SecurityAlertDispatcher(settings.auth_alert_command)
        self._button = ButtonPtt(
            on_press=self._on_ptt_press,
            on_release=self._on_ptt_release,
            on_double_click=self._on_double_click,
            on_cancel=self._on_cancel,
        )
        self._shutdown = threading.Event()
        self._auth_warning_stop = threading.Event()
        self._auth_warning_thread: threading.Thread | None = None
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
        self._run_startup_sequence()
        self._try_face_auth("起動時")
        self._set_ready_or_locked()
        self._announce_auth_required()
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
            if self._ensure_authenticated(text):
                self._greet_on_interaction()
                self._handle_text(text)

    def _run_startup_sequence(self) -> None:
        """起動状態を画面へ出し、設定に応じて起動音を鳴らす。"""
        if self._settings.startup_splash_enabled:
            self._dashboard_state.set_status("booting", "起動中")
        else:
            self._dashboard_state.set_status("ready", "待機中")
        if self._settings.startup_sound_enabled and not self._settings.dry_run:
            try:
                self._audio.play_wav(build_startup_chime(self._settings.voicevox_sample_rate))
            except Exception as exc:
                log.exception("起動音の再生に失敗しました")
                self._report_error("起動音", exc)
        if self._settings.startup_splash_enabled and self._settings.startup_splash_seconds > 0:
            time.sleep(self._settings.startup_splash_seconds)
        self._dashboard_state.set_status("ready", "待機中")

    def _greet_on_interaction(self) -> None:
        """設定に応じて発話処理前の挨拶を読み上げる。"""
        if self._greeting is None:
            return
        greeting = self._greeting.greeting_on_interaction()
        if greeting:
            self._speak_status(greeting)

    def _set_ready_or_locked(self) -> None:
        """認証状態に応じて待機表示またはロック表示へ切り替える。"""
        if self._auth.enabled and not self._auth.is_authenticated():
            self._dashboard_state.set_status("locked", "ロック中")
            return
        self._dashboard_state.set_status("ready", "待機中")

    def _announce_auth_required(self) -> None:
        """起動後に未認証なら本人確認を促す。"""
        if self._auth.enabled and not self._auth.is_authenticated():
            self._dashboard_state.set_status("locked", "ロック中")
            self._dashboard_state.add_error_notification("本人確認", "本人確認をしてください。")
            self._speak_status("本人確認をしてください。")
            self._start_auth_warning_timer(self._settings.auth_warning_delay_seconds)

    def _ensure_authenticated(self, transcript: str) -> bool:
        """未認証時は音声キーワードだけを検証し、Codex送信を止める。"""
        if self._auth.is_authenticated():
            self._auth.mark_activity()
            self._stop_auth_warning()
            return True
        if self._try_face_auth("顔認証"):
            return True
        result = self._auth.verify_keyword(transcript)
        if result.authenticated:
            self._stop_auth_warning()
            self._dashboard_state.set_status("ready", "待機中")
            self._speak_status(result.message)
            return False
        self._dashboard_state.set_status("locked", "ロック中")
        self._dashboard_state.add_error_notification("本人確認", result.message)
        if result.alert:
            self._dispatch_security_alert("本人確認", "本人確認に複数回失敗しました。")
        return False

    def _try_face_auth(self, source: str) -> bool:
        """顔認証が有効なら照合し、成功時は認証状態を延長する。"""
        if not self._auth.enabled or not self._face_auth.enabled or self._auth.is_authenticated():
            return False
        self._dashboard_state.set_status("authenticating", "本人確認中")
        result = self._face_auth.verify()
        if result.authenticated:
            self._auth.mark_activity()
            self._stop_auth_warning()
            self._dashboard_state.set_status("ready", "待機中")
            return True
        detail = result.message
        if result.score is not None:
            detail = f"{detail} スコア={result.score}"
        self._report_face_auth_failure(source, detail, getattr(result, "image_path", ""))
        return False

    def _report_face_auth_failure(self, source: str, detail: str, image_path: str = "") -> None:
        """顔認証失敗を、撮影画像があれば画像付き通知として表示する。"""
        image_url = ""
        if image_path:
            try:
                source_path = Path(image_path)
                if source_path.exists():
                    FACE_AUTH_FAILURE_IMAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source_path, FACE_AUTH_FAILURE_IMAGE_PATH)
                    image_url = f"{FACE_AUTH_FAILURE_IMAGE_URL}?t={int(time.time() * 1000)}"
            except OSError:
                log.exception("顔認証失敗画像の通知コピーに失敗しました")
        if image_url:
            self._dashboard_state.add_notification(
                title=f"{source} エラー",
                text=detail,
                source=source,
                priority="high",
                image_url=image_url,
            )
            return
        self._dashboard_state.add_error_notification(source, detail)

    def _dispatch_security_alert(self, source: str, message: str, image_path: str = "") -> None:
        """警戒通知をダッシュボードと外部アクションへ送る。"""
        self._dashboard_state.set_status("alert", "警戒中")
        self._dashboard_state.add_error_notification("警戒", message)
        self._start_auth_warning_timer(0, force_alert=True)
        result = self._security_alert.dispatch(source, message, image_path)
        if result.executed and not result.succeeded:
            self._dashboard_state.add_error_notification("警戒通知", result.message)

    def _start_auth_warning_timer(self, delay_seconds: float, force_alert: bool = False) -> None:
        """未認証が続いた場合に警告音を繰り返すタイマーを開始する。"""
        if self._settings.dry_run or not self._settings.auth_warning_sound_enabled:
            return
        if self._auth_warning_thread is not None and self._auth_warning_thread.is_alive():
            return
        self._auth_warning_stop.clear()
        self._auth_warning_thread = threading.Thread(
            target=self._run_auth_warning,
            args=(delay_seconds, force_alert),
            daemon=True,
        )
        self._auth_warning_thread.start()

    def _run_auth_warning(self, delay_seconds: float, force_alert: bool) -> None:
        """本人確認が終わるまで警告音を繰り返す。"""
        if self._auth_warning_stop.wait(max(0.0, delay_seconds)):
            return
        started_at = time.monotonic()
        alert_announced = False
        while not self._auth_warning_stop.is_set() and not self._auth.is_authenticated():
            alert_mode = force_alert or time.monotonic() - started_at + delay_seconds >= self._settings.auth_alert_delay_seconds
            if alert_mode:
                self._dashboard_state.set_status("alert", "警戒中")
                text = "警戒モードに入りました。本人確認してください。" if not alert_announced else "警戒モードです。本人確認してください。"
                alert_announced = True
            else:
                self._dashboard_state.set_status("locked", "ロック中")
                text = "本人確認してください。"
            self._play_auth_warning_sound()
            self._speak_status(text)
            if self._auth_warning_stop.wait(self._settings.auth_warning_interval_seconds):
                return

    def _stop_auth_warning(self) -> None:
        """本人確認完了時に警告音タイマーを止める。"""
        self._auth_warning_stop.set()
        if self._auth_warning_thread is not None and self._auth_warning_thread is not threading.current_thread():
            self._auth_warning_thread.join(timeout=2)

    def _play_auth_warning_sound(self) -> None:
        """本人確認失敗時の警告音を鳴らす。"""
        if self._settings.dry_run or not self._settings.auth_warning_sound_enabled:
            return
        try:
            self._audio.play_wav(build_auth_warning_tone(self._settings.voicevox_sample_rate))
        except Exception as exc:
            log.exception("本人確認警告音の再生に失敗しました")
            self._report_error("本人確認警告音", exc)

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
            try:
                transcript = self._transcribe_wav(wav_path)
            except Exception as exc:
                log.exception("文字起こしに失敗しました")
                self._report_error("文字起こし", exc)
                return
            if not transcript:
                return
            if self._ensure_authenticated(transcript):
                self._greet_on_interaction()
                self._handle_text(transcript)
        except Exception as exc:
            log.exception("音声処理に失敗しました")
            self._report_error("録音", exc)
            self._speak_status(f"処理に失敗しました。{exc}")
        finally:
            self._button.mark_idle()
            self._set_ready_or_locked()

    def _transcribe_wav(self, wav_path: str) -> str:
        """stt-gatewayを優先し、未設定または失敗時はfaster-whisperで文字起こしする。"""
        if self._settings.stt_gateway_url.strip():
            try:
                return self._stt.transcribe(wav_path)
            except Exception as exc:
                log.exception("stt-gatewayに失敗しました。faster-whisperへフォールバックします")
                self._report_error("stt-gateway", exc)
        return self._local_stt.transcribe(wav_path)

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
        except Exception as exc:
            log.exception("Codex 応答の取得に失敗しました")
            self._report_error("Codex", exc)
        finally:
            self._dashboard_state.finish_message(dashboard_message_id)
            if announcer is not None:
                announcer.stop()

    def _speak_response(self, text: str) -> None:
        """Codex 応答を tts-filter と TTS に通して再生する。"""
        if not text:
            return
        if self._settings.dry_run:
            print(f"ARGOS> {text}")
            return
        self._show_lcd(text)
        try:
            normalized = self._tts_filter.normalize(text)
        except Exception as exc:
            log.exception("TTSフィルターに失敗しました")
            self._report_error("TTSフィルター", exc)
            return
        try:
            wav_data = self._synthesize_tts(normalized)
        except Exception as exc:
            log.exception("TTSに失敗しました")
            return
        try:
            self._audio.play_wav(wav_data)
        except Exception as exc:
            log.exception("音声再生に失敗しました")
            self._report_error("音声再生", exc)

    def _speak_response_stream(self, deltas: Iterable[str], dashboard_message_id: str = "") -> str:
        """応答差分を句読点で分割し、TTS へ順次投入する。"""
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
                    log.info("キャンセル済みのため TTS チャンク投入を停止します")
                    break
                log.info("TTS チャンク投入: %s", chunk[:80])
                tts_queue.put(chunk)

        rest = chunker.flush()
        if rest and self._current_cancel_generation() == stream_generation:
            log.info("TTS 最終チャンク投入: %s", rest[:80])
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
        """TTS チャンクを順に合成して再生する。"""
        while True:
            chunk = tts_queue.get()
            if chunk is None:
                return
            if self._current_cancel_generation() != generation:
                self._drain_tts_queue(tts_queue)
                return
            self._show_lcd(chunk)
            self._dashboard_state.set_status("speaking", "読み上げ中")
            try:
                normalized = self._tts_filter.normalize(chunk)
            except Exception as exc:
                log.exception("TTSフィルターに失敗しました")
                self._report_error("TTSフィルター", exc)
                self._drain_tts_queue(tts_queue)
                return
            if self._current_cancel_generation() != generation:
                self._drain_tts_queue(tts_queue)
                return
            try:
                wav_data = self._synthesize_tts(normalized)
            except Exception as exc:
                log.exception("TTSに失敗しました")
                self._drain_tts_queue(tts_queue)
                return
            if self._current_cancel_generation() != generation:
                self._drain_tts_queue(tts_queue)
                return
            try:
                self._audio.play_wav(wav_data)
            except Exception as exc:
                log.exception("音声再生に失敗しました")
                self._report_error("音声再生", exc)
                self._drain_tts_queue(tts_queue)
                return
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
        except Exception as exc:
            log.exception("状態通知のTTSフィルターに失敗しました")
            self._report_error("TTSフィルター", exc)
            return
        try:
            wav_data = self._synthesize_tts(normalized)
        except Exception as exc:
            log.exception("状態通知のTTSに失敗しました")
            return
        try:
            self._audio.play_wav(wav_data)
        except Exception as exc:
            log.exception("状態通知の音声再生に失敗しました")
            self._report_error("音声再生", exc)

    def _synthesize_tts(self, text: str) -> bytes:
        """VOICEVOXを優先し、未設定または失敗時はKokoroで音声を生成する。"""
        if self._settings.voicevox_url.strip():
            try:
                return self._voicevox.synthesize(text)
            except Exception as exc:
                log.exception("VOICEVOXに失敗しました。Kokoroへフォールバックします")
                self._report_error("VOICEVOX", exc)
        try:
            return self._kokoro.synthesize(text)
        except Exception as exc:
            log.exception("Kokoroに失敗しました")
            self._report_error("Kokoro", exc)
            raise

    def _report_error(self, source: str, exc: Exception) -> None:
        """内部エラーをダッシュボードへ短い通知として表示する。"""
        text = str(exc).strip() or exc.__class__.__name__
        self._dashboard_state.set_status("error", "処理エラー")
        self._dashboard_state.add_error_notification(source, text[:300])

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
        if self._greeting is not None:
            self._greeting.mark_active()
        self._recorder.cancel()
        self._cancel_active_audio()
        self._stop_auth_warning()
        if self._dashboard_server is not None:
            self._dashboard_server.stop()
