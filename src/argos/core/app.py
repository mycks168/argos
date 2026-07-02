"""ARGOS のメイン制御。"""

from __future__ import annotations

import logging
import random
import re
import signal
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from argos.config import (
    DEFAULT_AGENT_PROGRESS_START_PHRASES,
    DEFAULT_AGENT_PROGRESS_WAIT_PHRASES,
    DEFAULT_WAKEWORD_ALIASES,
    Settings,
)
from argos.core.auth_coordinator import AuthCoordinator
from argos.core.speech_controller import SpeechController
from argos.core.status_controller import StatusController
from argos.hardware.audio import AudioInputStream, AudioPlayer, Recorder, StreamRecorder, check_audio_level, cleanup_stale_recordings
from argos.hardware.button import ButtonPtt
from argos.hardware.gpio import GpioPttInput
from argos.hardware.lcd import St7789TextDisplay
from argos.services.acknowledgement import AcknowledgementClient
from argos.services.agent import create_agent_client
from argos.services.agent.runner_client import RunnerSlotBusyError
from argos.services.agent_usage import AgentUsageProvider
from argos.services.audio_state import AudioStateStore
from argos.services.auth import AuthGate
from argos.services.dashboard.server import DashboardServer
from argos.services.dashboard.state import DashboardState
from argos.services.face_auth import FaceAuthVerifier
from argos.services.greeting import GreetingManager
from argos.services.network import read_wifi_status
from argos.services.security_alert import SecurityAlertDispatcher
from argos.services.startup import build_startup_chime
from argos.services.stt.gateway import SttGatewayClient
from argos.services.stt.whisper import FasterWhisperClient
from argos.services.tts.filter import TtsFilterClient
from argos.services.tts.cache import TTSCacheManager
from argos.services.tts.kokoro import KokoroClient
from argos.services.tts.voicevox import VoicevoxClient
from argos.services.wakeword import WakeWordListener


log = logging.getLogger(__name__)


# 旧名の後方互換エイリアス。既定フレーズは config 側に集約した。
CODEX_PROGRESS_START_PHRASES = DEFAULT_AGENT_PROGRESS_START_PHRASES
CODEX_PROGRESS_WAIT_PHRASES = DEFAULT_AGENT_PROGRESS_WAIT_PHRASES


class AgentProgressAnnouncer:
    """LLMエージェント待機中の進捗音声を管理する。"""

    def __init__(
        self,
        speak_status,
        first_delay_seconds: float,
        interval_seconds: float,
        user_text: str = "",
        acknowledgement_client: AcknowledgementClient | None = None,
        start_phrases: tuple[str, ...] = DEFAULT_AGENT_PROGRESS_START_PHRASES,
        wait_phrases: tuple[str, ...] = DEFAULT_AGENT_PROGRESS_WAIT_PHRASES,
    ) -> None:
        """読み上げ関数と通知間隔、進捗フレーズを初期化する。"""
        self._speak_status = speak_status
        self._first_delay_seconds = first_delay_seconds
        self._interval_seconds = interval_seconds
        self._user_text = user_text
        self._acknowledgement_client = acknowledgement_client
        self._start_phrases = start_phrases or DEFAULT_AGENT_PROGRESS_START_PHRASES
        self._wait_phrases = wait_phrases or DEFAULT_AGENT_PROGRESS_WAIT_PHRASES
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """開始メッセージを読み上げ、待機通知スレッドを起動する。"""
        if self._acknowledgement_client is not None and self._user_text:
            phrase = self._acknowledgement_client.select_phrase(self._user_text, self._start_phrases)
            self._speak_status(phrase)
        else:
            self._speak_random(self._start_phrases)
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
            self._speak_random(self._wait_phrases)
            if self._stop.wait(self._interval_seconds):
                return

    def _speak_random(self, phrases: tuple[str, ...]) -> None:
        """候補からランダムに1つ読み上げる。"""
        self._speak_status(random.choice(phrases))


# 旧名の後方互換エイリアス。
CodexProgressAnnouncer = AgentProgressAnnouncer


class ArgosApp:
    """PTT 録音からLLMエージェント応答の読み上げまでを束ねる。"""

    def __init__(self, settings: Settings) -> None:
        """各サービスクライアントと状態機械を初期化する。"""
        self._settings = settings
        cleanup_stale_recordings()
        audio_devices = settings.audio_input_devices or (settings.audio_input_device,)
        self._audio_input_stream = self._create_audio_input_stream(settings, audio_devices)
        if self._audio_input_stream is not None:
            self._recorder = StreamRecorder(self._audio_input_stream, settings.audio_sample_rate)
        else:
            self._recorder = Recorder(audio_devices, settings.audio_sample_rate)
        self._stt = SttGatewayClient(settings.stt_gateway_url, settings.stt_language, settings.stt_gateway_token)
        self._local_stt = FasterWhisperClient(
            settings.whisper_model_size,
            settings.stt_language,
            settings.whisper_device,
            settings.whisper_compute_type,
        )
        self._agent = create_agent_client(settings)
        self._agent_usage = AgentUsageProvider(
            settings.agent_usage_commands,
            settings.agent_usage_command_timeout_seconds,
        )
        self._tts_filter = TtsFilterClient(settings.tts_filter_url, settings.tts_filter_token)
        self._acknowledgement = AcknowledgementClient(settings.acknowledgement_url, settings.acknowledgement_token)
        self._voicevox = VoicevoxClient(
            settings.voicevox_url,
            settings.voicevox_speaker,
            settings.voicevox_sample_rate,
            settings.voicevox_speed_scale,
            settings.voicevox_volume_scale,
            settings.voicevox_bearer_token,
        )
        self._voicevox_speakers_by_slot_key = {
            _app_slot_key(slot.name, slot.provider): slot.voicevox_speaker
            for slot in settings.agent_slots
            if slot.voicevox_speaker is not None
        }
        self._kokoro = KokoroClient(
            settings.kokoro_voice,
            settings.kokoro_speed,
            settings.kokoro_repo_id,
            settings.kokoro_sample_rate,
        )
        self._tts_cache = TTSCacheManager(
            settings.tts_cache_dir,
            settings.tts_cache_max_chars,
            settings.tts_cache_max_size_mb,
        )
        self._audio_state = AudioStateStore(settings.audio_state_path)
        saved_audio_state = self._audio_state.load()
        initial_volume = saved_audio_state.volume if saved_audio_state.volume is not None else settings.audio_output_volume
        self._audio = AudioPlayer(settings.audio_output_device, settings.audio_output_card, initial_volume)
        self._lcd = self._create_lcd_display(settings)
        self._dashboard_state = DashboardState()
        self._dashboard_state.set_audio_volume(self._audio.volume)
        self._dashboard_state.set_slots([(slot.name, slot.provider) for slot in settings.agent_slots])
        self._sync_agent_display()
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
            should_record_short_press=self._is_auth_locked,
        )
        self._shutdown = threading.Event()
        self._status = StatusController(self._dashboard_state, self._auth.is_authenticated)
        initial_muted = saved_audio_state.muted if saved_audio_state.muted is not None else False
        self._speech = SpeechController(
            settings=settings,
            audio=self._audio,
            lcd=self._lcd,
            tts_filter=self._tts_filter,
            voicevox=self._voicevox,
            kokoro=self._kokoro,
            tts_cache=self._tts_cache,
            dashboard_state=self._dashboard_state,
            status=self._status,
            voicevox_speakers_by_slot_key=self._voicevox_speakers_by_slot_key,
            current_slot_key=lambda: _app_slot_key(self._agent.current_name, self._agent.current_provider),
            is_current_slot=self._is_current_slot_key,
            report_error=self._report_error,
            shutdown=self._shutdown,
            muted=initial_muted,
        )
        self._auth_coord = AuthCoordinator(
            settings=settings,
            auth=self._auth,
            face_auth=self._face_auth,
            security_alert=self._security_alert,
            status=self._status,
            dashboard_state=self._dashboard_state,
            speak_status=lambda text: self._speech.speak_status(text),
            audio=self._audio,
            is_recording=lambda: self._recorder.is_recording,
            report_error=self._report_error,
            shutdown=self._shutdown,
        )
        self._microphone_enabled = True
        self._pending_slot_speech: dict[str, str] = {}
        self._pending_speech_thread: threading.Thread | None = None
        self._agent_delivery_thread: threading.Thread | None = None
        self._agent_usage_thread: threading.Thread | None = None
        self._wifi_status_thread: threading.Thread | None = None
        self._worker: threading.Thread | None = None
        self._wakeword_listener: WakeWordListener | None = None
        self._wakeword_recording = threading.Event()
        self._wakeword_ptt_hold = threading.Event()
        self._gpio: GpioPttInput | None = None
        self._wakeword_pattern = build_wakeword_pattern(settings.wakeword_aliases)
        self._dashboard_state.set_audio_muted(initial_muted)

    def _create_audio_input_stream(self, settings: Settings, audio_devices: Iterable[str]) -> AudioInputStream | None:
        """ウェイクワード有効時にPTTと共有するマイク入力を作成する。"""
        if settings.dry_run or not settings.wakeword_enabled:
            return None
        return AudioInputStream(
            audio_devices,
            settings.wakeword_capture_sample_rate,
            settings.audio_sample_rate,
            chunk_ms=settings.wakeword_chunk_ms,
        )

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
            camera_snapshot_path=Path(settings.camera_snapshot_path).expanduser(),
            screensaver_seconds=settings.dashboard_screensaver_seconds,
            default_font_size=settings.dashboard_default_font_size,
            location_provider=settings.location_provider,
            remote_location_url=settings.remote_location_url,
            remote_location_timeout_seconds=settings.remote_location_timeout_seconds,
            control_handler=self._handle_dashboard_control,
            event_handler=self._handle_dashboard_event,
        )

    def run(self) -> None:
        """ARGOS を起動し、終了シグナルまで待機する。"""
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)
        log.info("ARGOS 起動: provider=%s 現在のエージェントスロット=%s", self._settings.agent_provider, self._agent.current_name)
        if self._dashboard_server is not None:
            self._dashboard_server.start()
        self._run_startup_sequence()
        self._auth_coord.try_face_auth("起動時", self._status.current_generation())
        self._set_ready_or_locked()
        if not self._settings.dry_run and self._settings.ptt_gpio is not None:
            self._gpio = GpioPttInput(self._settings.ptt_gpio, self._button.handle_press, self._button.handle_release)
        elif not self._settings.dry_run:
            log.info("ARGOS_PTT_GPIO が未設定のためGPIO PTT入力を無効化します")
        self._start_wakeword_listener()
        self._auth_coord.announce_required()
        self._auth_coord.start_status_monitor()
        self._start_agent_delivery_monitor()
        self._start_agent_usage_monitor()
        self._start_wifi_status_monitor()
        if self._settings.dry_run:
            self._run_text_loop()
            return
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
                name = self._agent.next_slot()
                self._sync_agent_display()
                self._set_ready_or_locked()
                self._speech.speak_status(f"{name}に切り替えました")
                continue
            if text == "/reset":
                self._agent.reset_current()
                self._speech.speak_status("現在のセッションを新規会話にしました")
                continue
            if self._auth_coord.ensure_authenticated(text, self._status.current_generation()):
                self._greet_on_interaction()
                self._handle_text(text)

    def _start_agent_delivery_monitor(self) -> None:
        """Runner側で完了した未配信ジョブをダッシュボードへ反映する。"""
        if not hasattr(self._agent, "list_undelivered") or not hasattr(self._agent, "mark_delivered"):
            return
        self._agent_delivery_thread = threading.Thread(target=self._run_agent_delivery_monitor, daemon=True)
        self._agent_delivery_thread.start()

    def _run_agent_delivery_monitor(self) -> None:
        """Agent Runnerの未配信結果を定期的に回収する。"""
        while not self._shutdown.is_set():
            try:
                list_undelivered = getattr(self._agent, "list_undelivered")
                mark_delivered = getattr(self._agent, "mark_delivered")
                for job in list_undelivered():
                    if not isinstance(job, dict):
                        continue
                    job_id = str(job.get("job_id", ""))
                    slot_name = str(job.get("slot_name", ""))
                    provider = str(job.get("provider", ""))
                    status = str(job.get("status", ""))
                    result = str(job.get("result", "")).strip()
                    error = str(job.get("error", "")).strip()
                    if status == "completed" and result:
                        self._deliver_runner_result(job_id, slot_name, provider, result)
                        mark_delivered(job_id)
                    elif status == "failed":
                        self._deliver_runner_error(job_id, slot_name, provider, error)
                        mark_delivered(job_id)
            except Exception:
                log.exception("Agent Runner未配信ジョブの確認に失敗しました")
            self._shutdown.wait(5)

    def _deliver_runner_result(self, job_id: str, slot_name: str, provider: str, result: str) -> None:
        """Runnerで完了した応答を会話履歴と通知へ反映する。"""
        slot_key = _app_slot_key(slot_name, provider)
        self._dashboard_state.add_message_to_slot(slot_name, provider, "assistant", result)
        self._pending_slot_speech[slot_key] = result
        if self._is_current_slot_key(slot_key):
            self._start_pending_slot_response()
        else:
            self._dashboard_state.set_slot_unread(slot_name, provider, True)
        self._dashboard_state.add_notification(
            f"{slot_name} 応答完了",
            "Runnerで完了した応答を会話履歴に反映しました。",
            source="ARGOS",
        )
        log.info("Agent Runner未配信応答を反映しました: job_id=%s slot=%s provider=%s", job_id, slot_name, provider)

    def _deliver_runner_error(self, job_id: str, slot_name: str, provider: str, error: str) -> None:
        """Runnerで失敗したジョブを通知へ反映する。"""
        text = error or "Agent Runnerジョブに失敗しました"
        self._dashboard_state.add_error_notification(f"{slot_name} Runner", text[:300])
        log.info("Agent Runner未配信エラーを反映しました: job_id=%s slot=%s provider=%s", job_id, slot_name, provider)

    def _run_startup_sequence(self) -> None:
        """起動状態を画面へ出し、設定に応じて起動音を鳴らす。"""
        if self._settings.startup_splash_enabled:
            self._status.set_display("booting", "起動中")
        else:
            self._status.set_display("ready", "待機中")
        if self._settings.startup_sound_enabled and not self._settings.dry_run:
            try:
                self._audio.play_wav(build_startup_chime(self._settings.voicevox_sample_rate))
            except Exception as exc:
                log.exception("起動音の再生に失敗しました")
                self._report_error("起動音", exc)
        if self._settings.startup_splash_enabled and self._settings.startup_splash_seconds > 0:
            time.sleep(self._settings.startup_splash_seconds)
        self._status.set_display("ready", "待機中")

    def _greet_on_interaction(self) -> None:
        """設定に応じて発話処理前の挨拶を読み上げる。"""
        if self._greeting is None:
            return
        greeting = self._greeting.greeting_on_interaction()
        if greeting:
            self._speech.speak_status(greeting)

    def _set_ready_or_locked(self) -> None:
        """認証状態に応じて待機表示またはロック表示へ切り替える。"""
        self._status.force_resting()

    def _sync_agent_display(self) -> None:
        """現在のエージェントスロットをダッシュボード表示へ反映する。"""
        self._dashboard_state.set_agent(self._agent.current_name, self._agent.current_provider)
        self._publish_agent_usage_pending()
        self._refresh_current_agent_usage()

    def _start_agent_usage_monitor(self) -> None:
        """現在エージェントの利用枠を定期的に取得する。"""
        if not self._agent_usage.providers:
            return
        self._agent_usage_thread = threading.Thread(target=self._run_agent_usage_monitor, daemon=True)
        self._agent_usage_thread.start()

    def _run_agent_usage_monitor(self) -> None:
        """利用枠取得コマンドを一定間隔で実行する。"""
        while not self._shutdown.is_set():
            self._refresh_current_agent_usage()
            interval = max(10.0, self._settings.agent_usage_refresh_seconds)
            if self._shutdown.wait(interval):
                return

    def _publish_agent_usage_pending(self) -> None:
        """取得対象プロバイダなら、初期表示として取得待ちを出す。"""
        provider = self._agent.current_provider
        if not self._agent_usage.has_provider(provider) or self._dashboard_state.has_agent_usage(provider):
            return
        self._dashboard_state.set_agent_usage(
            provider,
            {
                "provider": provider.lower(),
                "available": False,
                "label": "取得待ち",
                "five_hour": None,
                "weekly": None,
                "other_text": "",
                "error": "",
            },
        )

    def _refresh_current_agent_usage(self) -> None:
        """現在プロバイダの利用枠を取得してダッシュボードへ反映する。"""
        provider = self._agent.current_provider
        if not self._agent_usage.has_provider(provider):
            return
        snapshot = self._agent_usage.fetch(provider)
        self._dashboard_state.set_agent_usage(provider, snapshot.to_dict())

    def _start_wifi_status_monitor(self) -> None:
        """Wi-Fi接続状態を定期的にダッシュボードへ反映する。"""
        if self._dashboard_server is None:
            return
        self._wifi_status_thread = threading.Thread(target=self._run_wifi_status_monitor, daemon=True)
        self._wifi_status_thread.start()

    def _run_wifi_status_monitor(self) -> None:
        """Wi-Fi接続状態を一定間隔で取得する。"""
        while not self._shutdown.is_set():
            self._refresh_wifi_status()
            interval = max(2.0, self._settings.wifi_status_refresh_seconds)
            if self._shutdown.wait(interval):
                return

    def _refresh_wifi_status(self) -> None:
        """現在のWi-Fi状態をダッシュボード状態へ反映する。"""
        try:
            self._dashboard_state.set_wifi_status(read_wifi_status().to_dict())
        except Exception:
            log.exception("Wi-Fi状態の取得に失敗しました")

    def _on_ptt_press(self) -> None:
        """PTT 押下時に録音を開始する。"""
        if not self._is_microphone_enabled():
            log.info("マイクOFFのためPTT押下を無視します")
            return
        log.info("PTT ON: 録音開始")
        if self._wakeword_recording.is_set():
            log.info("ウェイクワード録音中のPTT押下を発話継続として扱います")
            self._wakeword_ptt_hold.set()
            generation = self._cancel_active_audio()
            if self._is_auth_locked():
                self._status.set(generation, "auth_listening", "本人確認録音中")
            else:
                self._status.set(generation, "listening", "録音中")
            return
        generation = self._cancel_active_audio()
        if self._is_auth_locked():
            self._status.set(generation, "auth_listening", "本人確認録音中")
            if self._auth.has_authenticated_once:
                self._auth_coord.play_lock_warning()
        else:
            self._status.set(generation, "listening", "録音中")
        self._recorder.start()

    def _on_ptt_release(self) -> None:
        """PTT 解放時に録音を停止し、処理スレッドを開始する。"""
        if not self._is_microphone_enabled():
            log.info("マイクOFFのためPTT解放を無視します")
            return
        log.info("PTT OFF: 録音停止と処理開始")
        if self._wakeword_ptt_hold.is_set():
            log.info("ウェイクワード録音のPTT継続を解除します")
            self._wakeword_ptt_hold.clear()
            return
        generation = self._status.current_generation()
        if self._is_auth_locked():
            self._status.set(generation, "authenticating", "本人確認中")
        else:
            self._status.set(generation, "transcribing", "文字起こし中")
        self._worker = threading.Thread(target=self._process_recording, daemon=True)
        self._worker.start()

    def _on_double_click(self) -> None:
        """ダブルクリックでエージェントスロットを切り替える。"""
        self._audio.cancel()
        name = self._agent.next_slot()
        self._sync_agent_display()
        self._set_ready_or_locked()
        log.info("エージェントスロット切替: %s", name)
        self._speech.speak_status(f"{name}に切り替えました")
        self._start_pending_slot_response()

    def _on_cancel(self) -> None:
        """処理中の音声入出力をキャンセルする。"""
        log.info("キャンセル要求: 録音破棄と再生停止")
        self._recorder.cancel()
        self._cancel_active_audio()
        self._set_ready_or_locked()

    def _start_wakeword_listener(self) -> None:
        """設定されていればLiveKitウェイクワード監視を開始する。"""
        if not self._settings.wakeword_enabled or self._settings.dry_run:
            return
        try:
            self._wakeword_listener = WakeWordListener(
                devices=self._settings.audio_input_devices or (self._settings.audio_input_device,),
                model_dir=self._settings.wakeword_model_dir,
                threshold=self._settings.wakeword_threshold,
                audio_source=self._audio_input_stream,
                should_continue_recording=self._should_continue_wakeword_recording,
                capture_sample_rate=self._settings.wakeword_capture_sample_rate,
                window_seconds=self._settings.wakeword_window_seconds,
                interval_seconds=self._settings.wakeword_interval_seconds,
                chunk_ms=self._settings.wakeword_chunk_ms,
                record_min_seconds=self._settings.wakeword_record_min_seconds,
                record_max_seconds=self._settings.wakeword_record_max_seconds,
                record_silence_seconds=self._settings.wakeword_record_silence_seconds,
                pre_roll_seconds=self._settings.wakeword_pre_roll_seconds,
                min_actual_seconds=self._settings.wakeword_min_actual_seconds,
                silence_rms_threshold=self._settings.silence_rms_threshold,
                endpoint_mode=self._settings.wakeword_endpoint_mode,
                vad_model_path=self._settings.wakeword_vad_model_path,
                vad_threshold=self._settings.wakeword_vad_threshold,
                vad_min_silence_seconds=self._settings.wakeword_vad_min_silence_seconds,
                vad_check_seconds=self._settings.wakeword_vad_check_seconds,
                score_log_path=self._settings.wakeword_score_log_path,
                on_detected=self._on_wakeword_detected,
                on_recording_ready=self._on_wakeword_recording_ready,
            )
            self._wakeword_listener.start()
            self._dashboard_state.add_notification("ウェイクワード", "ウェイクワード監視を開始しました。", source="ARGOS")
        except Exception as exc:
            log.exception("ウェイクワード監視を開始できません")
            self._report_error("ウェイクワード", exc)

    def _on_wakeword_detected(self) -> bool:
        """ウェイクワード検知時に画面状態と音声出力を録音向けへ切り替える。"""
        if self._shutdown.is_set():
            return False
        if not self._is_microphone_enabled():
            log.info("マイクOFFのためウェイクワード検知を無視します")
            return False
        if self._recorder.is_recording:
            log.info("PTT録音中のためウェイクワード検知を無視します")
            return False
        status_code = self._dashboard_state.snapshot().get("status", {}).get("code")
        if status_code not in ("ready", "locked"):
            log.info("受付可能状態ではないためウェイクワード検知を無視します: status=%s", status_code)
            return False
        if getattr(self._audio, "is_playing", False) or status_code == "speaking":
            log.info("読み上げ中のためウェイクワード検知を無視します")
            return False
        if self._speech.is_tts_cooldown_active():
            log.info("読み上げ直後のためウェイクワード検知を無視します")
            return False
        log.info("ウェイクワード検知を受け取りました")
        self._wakeword_recording.set()
        self._wakeword_ptt_hold.clear()
        generation = self._cancel_active_audio()
        if self._is_auth_locked():
            self._status.set(generation, "auth_listening", "本人確認録音中")
        else:
            self._status.set(generation, "listening", "録音中")
        return True

    def _on_wakeword_recording_ready(self, wav_path: str) -> None:
        """ウェイクワード後に録音されたWAVを処理スレッドへ渡す。"""
        self._wakeword_recording.clear()
        self._wakeword_ptt_hold.clear()
        if self._shutdown.is_set() or not self._is_microphone_enabled():
            self._remove_recording_file(wav_path)
            return
        generation = self._status.current_generation()
        if self._is_auth_locked():
            self._status.set(generation, "authenticating", "本人確認中")
        else:
            self._status.set(generation, "transcribing", "文字起こし中")
        self._worker = threading.Thread(target=self._process_wakeword_recording, args=(wav_path,), daemon=True)
        self._worker.start()

    def _process_wakeword_recording(self, wav_path: str) -> None:
        """ウェイクワード検知後のWAVをSTT、LLMエージェント、TTSの順に処理する。"""
        token = self._status.current_generation()
        try:
            level = check_audio_level(wav_path)
            log.info("ウェイクワード後録音音量: RMS=%.1f", level)
            try:
                transcript = self._transcribe_wav(wav_path)
            except Exception as exc:
                log.exception("ウェイクワード後の文字起こしに失敗しました")
                self._report_error("文字起こし", exc)
                return
            if not transcript:
                log.info("ウェイクワード後の文字起こし結果が空でした: wav=%s RMS=%.1f", wav_path, level)
                self._dashboard_state.add_error_notification("文字起こし", "音声を認識できませんでした。")
                return
            if self._settings.wakeword_require_stt_wakeword and not _has_leading_wakeword(transcript, self._wakeword_pattern):
                log.info("STT結果に呼びかけがないためウェイクワード検知を破棄します: %s", transcript)
                self._dashboard_state.add_notification("ウェイクワード", "呼びかけを確認できなかったため破棄しました。", source="ARGOS")
                return
            transcript = _strip_leading_wakeword(transcript, self._wakeword_pattern)
            if not transcript:
                log.info("ウェイクワード除去後の文字起こし結果が空でした: wav=%s RMS=%.1f", wav_path, level)
                self._dashboard_state.add_error_notification("文字起こし", "呼びかけ以外の音声を認識できませんでした。")
                return
            if self._auth_coord.ensure_authenticated(transcript, token):
                self._greet_on_interaction()
                self._handle_text(transcript)
        except Exception as exc:
            log.exception("ウェイクワード後の音声処理に失敗しました")
            self._report_error("録音", exc)
            self._speech.speak_status(f"処理に失敗しました。{exc}")
        finally:
            self._wakeword_recording.clear()
            self._wakeword_ptt_hold.clear()
            self._remove_recording_file(wav_path)
            self._status.finish(token)

    def _should_continue_wakeword_recording(self) -> bool:
        """PTT押下でウェイクワード後録音を継続するか返す。"""
        return self._wakeword_ptt_hold.is_set()

    def _cancel_active_audio(self) -> int:
        """再生中と未再生の読み上げチャンクを無効化し、新しい世代を返す。"""
        generation = self._status.invalidate()
        self._audio.cancel()
        return generation

    def _current_cancel_generation(self) -> int:
        """現在の世代トークンを返す。"""
        return self._status.current_generation()

    def _handle_dashboard_control(self, payload: dict[str, object]) -> dict[str, object]:
        """ダッシュボードからの操作をARGOS本体へ反映する。"""
        action = str(payload.get("action", ""))
        if action == "mute":
            self._set_muted(True)
        elif action == "unmute":
            self._set_muted(False)
        elif action == "toggle_mute":
            self._set_muted(not self._speech.is_muted())
        elif action == "set_volume":
            volume = self._audio.set_volume(int(payload.get("volume", self._audio.volume)))
            self._dashboard_state.set_audio_volume(volume)
            self._save_audio_state()
        elif action == "enable_microphone":
            self._set_microphone_enabled(True)
        elif action == "disable_microphone":
            self._set_microphone_enabled(False)
        elif action == "toggle_microphone":
            self._set_microphone_enabled(not self._is_microphone_enabled())
        elif action == "reset_agent_session":
            slot_name = self._agent.current_name
            slot_provider = self._agent.current_provider
            self._agent.reset_current()
            self._dashboard_state.add_notification(
                "セッションリセット",
                f"{slot_name} の次回エージェント呼び出しを新規セッションにします。",
                source="ARGOS",
            )
            return {
                "muted": self._speech.is_muted(),
                "volume": self._audio.volume,
                "session_reset": True,
                "slot": {"name": slot_name, "provider": slot_provider},
            }
        else:
            raise ValueError(f"未対応の操作です: {action}")
        return {"muted": self._speech.is_muted(), "volume": self._audio.volume, "microphone_enabled": self._is_microphone_enabled()}

    def _handle_dashboard_event(self, payload: dict[str, object], response: dict[str, object]) -> None:
        """外部表示イベントに応じて通知音や読み上げを行う。"""
        if payload.get("type") != "notification":
            return
        if not payload.get("sound") and not payload.get("speak"):
            return
        thread = threading.Thread(target=self._announce_dashboard_notification, args=(payload,), daemon=True)
        thread.start()

    def _announce_dashboard_notification(self, payload: dict[str, object]) -> None:
        """外部通知を音と音声で知らせる。"""
        self._dashboard_state.wake_display()
        self._wait_for_notification_audio_slot()
        if payload.get("sound") and not self._settings.dry_run:
            try:
                self._audio.play_wav(build_startup_chime(self._settings.voicevox_sample_rate))
            except Exception as exc:
                log.exception("通知音の再生に失敗しました")
                self._report_error("通知音", exc)
        if payload.get("speak"):
            title = str(payload.get("title", "")).strip()
            text = str(payload.get("text", "")).strip()
            phrase = "。".join(part for part in (title, text) if part)
            if phrase:
                self._wait_for_notification_audio_slot()
                self._speech.speak_status(phrase)

    def _wait_for_notification_audio_slot(self) -> None:
        """通知音声が他の読み上げと重ならないよう、再生中なら待つ。"""
        while not self._shutdown.is_set() and getattr(self._audio, "is_playing", False):
            time.sleep(0.2)

    def _process_recording(self) -> None:
        """録音済み WAV を STT、LLMエージェント、TTS の順に処理する。"""
        token = self._status.current_generation()
        wav_path = ""
        try:
            wav_path = self._recorder.stop()
            level = check_audio_level(wav_path)
            log.info("PTT録音音量: RMS=%.1f", level)
            try:
                transcript = self._transcribe_wav(wav_path)
            except Exception as exc:
                log.exception("文字起こしに失敗しました")
                self._report_error("文字起こし", exc)
                return
            if not transcript:
                log.info("文字起こし結果が空でした: wav=%s RMS=%.1f", wav_path, level)
                self._dashboard_state.add_error_notification("文字起こし", "音声を認識できませんでした。")
            if self._auth_coord.ensure_authenticated(transcript, token):
                self._greet_on_interaction()
                self._handle_text(transcript)
        except Exception as exc:
            log.exception("音声処理に失敗しました")
            self._report_error("録音", exc)
            self._speech.speak_status(f"処理に失敗しました。{exc}")
        finally:
            if wav_path:
                self._remove_recording_file(wav_path)
            self._button.mark_idle()
            self._status.finish(token)

    def _remove_recording_file(self, wav_path: str) -> None:
        """処理済みの録音ファイルを削除する。"""
        try:
            Path(wav_path).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("処理済み録音ファイルを削除できませんでした: %s: %s", wav_path, exc)

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
        """テキストをLLMエージェントに送り、応答を読み上げる。"""
        log.info("ユーザ発話: %s", text)
        slot_name = self._agent.current_name
        slot_provider = self._agent.current_provider
        slot_key = _app_slot_key(slot_name, slot_provider)
        handle_generation = self._current_cancel_generation()
        self._dashboard_state.add_message("user", text)
        self._dashboard_state.set_slot_busy(slot_name, slot_provider, True)
        self._status.set(handle_generation, "thinking", "考え中")
        announcer = self._start_agent_progress(text, slot_key)
        dashboard_message_id = self._dashboard_state.add_message("assistant", "", streaming=True)
        try:
            response = self._speech.speak_response_stream(
                self._stop_progress_on_first_delta(self._agent.ask_stream(text), announcer),
                dashboard_message_id=dashboard_message_id,
                slot_key=slot_key,
            )
            log.info("エージェント応答: %s", response[:300])
            if response and not self._is_current_slot_key(slot_key):
                self._pending_slot_speech[slot_key] = response
                self._dashboard_state.set_slot_unread(slot_name, slot_provider, True)
                self._dashboard_state.add_notification(f"{slot_name} 応答完了", "スロットを切り替えると読み上げます。", source="ARGOS")
        except RunnerSlotBusyError as exc:
            log.info("エージェントスロットが処理中です: %s", exc)
            self._speech.speak_status("前の応答がまだ処理中だよ。少し待ってね。")
        except Exception as exc:
            log.exception("エージェント応答の取得に失敗しました")
            self._report_error("エージェント", exc)
            exc_msg = str(exc).lower()
            if "rate limit" in exc_msg or "quota" in exc_msg or "limit" in exc_msg:
                self._speech.speak_status("リミット制限に達しました。")
            else:
                self._speech.speak_status("エージェントの応答取得に失敗しました。")
        finally:
            self._dashboard_state.set_slot_busy(slot_name, slot_provider, False)
            self._dashboard_state.finish_message(dashboard_message_id)
            if announcer is not None:
                announcer.stop()
            self._status.finish(handle_generation)

    def _start_agent_progress(self, user_text: str = "", slot_key: str = "") -> AgentProgressAnnouncer | None:
        """設定に応じてエージェント待機中の進捗音声を開始する。"""
        if not self._settings.agent_progress_voice:
            return None
        def speak_if_current(text: str) -> None:
            """現在スロットの待機通知だけ読み上げる。"""
            if not slot_key or self._is_current_slot_key(slot_key):
                self._speech.speak_status(text)
        announcer = AgentProgressAnnouncer(
            speak_status=speak_if_current,
            first_delay_seconds=self._settings.agent_progress_first_delay_seconds,
            interval_seconds=self._settings.agent_progress_interval_seconds,
            user_text=user_text,
            acknowledgement_client=self._acknowledgement,
            start_phrases=self._settings.agent_progress_start_phrases,
            wait_phrases=self._settings.agent_progress_wait_phrases,
        )
        announcer.start()
        return announcer

    # 旧名の後方互換エイリアス。
    _start_codex_progress = _start_agent_progress

    def _stop_progress_on_first_delta(
        self,
        deltas: Iterable[str],
        announcer: AgentProgressAnnouncer | None,
    ) -> Iterable[str]:
        """エージェント本文が届いた時点で進捗音声を止める。"""
        stopped = False
        for delta in deltas:
            if not stopped and announcer is not None:
                announcer.stop()
                stopped = True
            yield delta

    def _report_error(self, source: str, exc: Exception) -> None:
        """内部エラーをダッシュボードへ短い通知として表示する。"""
        text = str(exc).strip() or exc.__class__.__name__
        self._status.set_display("error", "処理エラー")
        self._dashboard_state.add_error_notification(source, text[:300])

    def _set_muted(self, muted: bool) -> None:
        """ダッシュボード操作による読み上げミュート状態を更新する。"""
        changed = self._speech.set_muted(muted)
        self._dashboard_state.set_audio_muted(muted)
        self._save_audio_state()
        if muted:
            if self._dashboard_state.status_code() == "speaking":
                self._set_ready_or_locked()
            if changed:
                self._dashboard_state.add_notification("ミュート", "読み上げを一時停止しました。", source="ARGOS")
            return
        if changed:
            self._dashboard_state.add_notification("ミュート解除", "読み上げを再開します。", source="ARGOS")

    def _set_microphone_enabled(self, enabled: bool) -> None:
        """ダッシュボード操作によるマイク受付状態を更新する。"""
        changed = self._microphone_enabled != enabled
        self._microphone_enabled = enabled
        if not enabled:
            self._recorder.cancel()
            self._wakeword_recording.clear()
            self._wakeword_ptt_hold.clear()
            self._set_ready_or_locked()
        self._dashboard_state.set_microphone_enabled(enabled)
        if changed:
            title = "マイクON" if enabled else "マイクOFF"
            message = "マイク入力を受け付けます。" if enabled else "マイク入力を停止しました。"
            self._dashboard_state.add_notification(title, message, source="ARGOS")

    def _is_microphone_enabled(self) -> bool:
        """マイク入力を受け付ける状態ならTrueを返す。"""
        return self._microphone_enabled

    def _save_audio_state(self) -> None:
        """現在の読み上げ音量とミュート状態を保存する。"""
        try:
            self._audio_state.save(self._audio.volume, self._speech.is_muted())
        except OSError as exc:
            log.exception("音声状態の保存に失敗しました")
            self._report_error("音声状態保存", exc)

    def _is_current_slot_key(self, slot_key: str) -> bool:
        """指定スロットが現在表示中ならTrueを返す。"""
        return slot_key == _app_slot_key(self._agent.current_name, self._agent.current_provider)

    def _start_pending_slot_response(self) -> None:
        """現在スロットの未読応答を、PTT処理を塞がないよう別スレッドで読み上げる。"""
        slot_key = _app_slot_key(self._agent.current_name, self._agent.current_provider)
        response = self._pending_slot_speech.pop(slot_key, "")
        if response:
            self._dashboard_state.set_slot_unread(self._agent.current_name, self._agent.current_provider, False)
            self._pending_speech_thread = threading.Thread(
                target=self._speak_pending_slot_response,
                args=(response, slot_key),
                daemon=True,
            )
            self._pending_speech_thread.start()

    def _speak_pending_slot_response(self, response: str, slot_key: str) -> None:
        """未読応答を通常応答と同じチャンク分割とキャンセル制御で読み上げる。"""
        token = self._status.current_generation()
        try:
            self._speech.speak_response_stream([response], slot_key=slot_key)
        finally:
            self._status.finish(token)

    def _is_auth_locked(self) -> bool:
        """本人確認が必要なロック状態ならTrueを返す。"""
        return self._auth_coord.is_locked()

    def _handle_signal(self, signum: int, _frame: object) -> None:
        """終了シグナルを受けて停止する。"""
        log.info("終了シグナルを受信しました: %s", signum)
        self._shutdown.set()
        if self._greeting is not None:
            self._greeting.mark_active()
        self._recorder.cancel()
        if self._wakeword_listener is not None:
            self._wakeword_listener.stop()
        if self._audio_input_stream is not None:
            self._audio_input_stream.stop()
        self._cancel_active_audio()
        self._auth_coord.stop_warning()
        if self._dashboard_server is not None:
            self._dashboard_server.stop()


def _app_slot_key(name: str, provider: str) -> str:
    """アプリ内部で使うスロットキーを作る。"""
    return f"{provider}\0{name}"


def build_wakeword_pattern(aliases: tuple[str, ...]) -> re.Pattern[str]:
    """設定されたウェイクワード別名から、先頭の呼びかけを検出する正規表現を作る。"""
    alternatives = "|".join(re.escape(alias) for alias in aliases if alias) or "(?!)"
    return re.compile(rf"^\s*(?:{alternatives})[\s、。,.，．:：!！?？-]*")


# 既定別名から作った後方互換用のモジュールパターン。
_LEADING_WAKEWORD_PATTERN = build_wakeword_pattern(DEFAULT_WAKEWORD_ALIASES)


def _strip_leading_wakeword(text: str, pattern: re.Pattern[str] = _LEADING_WAKEWORD_PATTERN) -> str:
    """ウェイクワード経由STTの先頭に混ざった呼びかけだけを除去する。"""
    return pattern.sub("", text, count=1).strip()


def _has_leading_wakeword(text: str, pattern: re.Pattern[str] = _LEADING_WAKEWORD_PATTERN) -> bool:
    """STT結果が呼びかけから始まるかを判定する。"""
    return bool(pattern.match(text))
