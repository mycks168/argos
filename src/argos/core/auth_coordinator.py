"""本人確認フローと警戒モードを管理する。

音声キーワード・顔認証による本人確認、認証期限切れの監視、未認証が続いた
場合の警告音・警戒モード遷移をまとめる。ArgosApp はロック判定と発話処理前の
本人確認だけを呼び出し、認証の詳細はこのコーディネーターに閉じ込める。
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path

from argos.config import Settings
from argos.core.status_controller import StatusController
from argos.services.auth import AuthGate
from argos.services.dashboard.state import DashboardState
from argos.services.face_auth import FaceAuthVerifier
from argos.services.security_alert import SecurityAlertDispatcher
from argos.services.startup import build_auth_warning_tone


log = logging.getLogger(__name__)
FACE_AUTH_FAILURE_IMAGE_URL = "/camera/latest.jpg"


class AuthCoordinator:
    """本人確認・顔認証・警告音・警戒モードを一元管理する。"""

    def __init__(
        self,
        *,
        settings: Settings,
        auth: AuthGate,
        face_auth: FaceAuthVerifier,
        security_alert: SecurityAlertDispatcher,
        status: StatusController,
        dashboard_state: DashboardState,
        speak_status: Callable[[str], None],
        audio,
        is_recording: Callable[[], bool],
        report_error: Callable[[str, Exception], None],
        shutdown: threading.Event,
    ) -> None:
        """認証に必要なサービスとコールバックを保持する。"""
        self._settings = settings
        self._auth = auth
        self._face_auth = face_auth
        self._security_alert = security_alert
        self._status = status
        self._dashboard_state = dashboard_state
        self._speak_status = speak_status
        self._audio = audio
        self._is_recording = is_recording
        self._report_error = report_error
        self._shutdown = shutdown
        self._warning_stop = threading.Event()
        self._warning_thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        """本人確認が有効か返す。"""
        return self._auth.enabled

    def is_locked(self) -> bool:
        """本人確認が必要なロック状態ならTrueを返す。"""
        return self._auth.enabled and not self._auth.is_authenticated()

    def ensure_authenticated(self, transcript: str, token: int) -> bool:
        """未認証時は音声キーワードだけを検証し、エージェント送信を止める。

        token はこの発話処理を開始した対話の世代トークン。状態更新はこの世代が
        現行のときだけ反映し、新しい発話が始まっていれば上書きしない。
        """
        if self._auth.is_authenticated():
            self._auth.mark_activity()
            self.stop_warning()
            return True
        if self.try_face_auth("顔認証", token):
            return True
        result = self._auth.verify_keyword(transcript)
        log.info("本人確認キーワード照合: transcript=%r authenticated=%s message=%s", transcript, result.authenticated, result.message)
        if result.authenticated:
            self.stop_warning()
            self._status.set(token, "ready", "待機中")
            self._speak_status(result.message)
            return False
        self._status.set(token, "locked", "ロック中")
        self._dashboard_state.add_error_notification("本人確認", result.message)
        if result.alert:
            self._dispatch_security_alert("本人確認", "本人確認に複数回失敗しました。")
        return False

    def try_face_auth(self, source: str, token: int) -> bool:
        """顔認証が有効なら照合し、成功時は認証状態を延長する。"""
        if not self._auth.enabled or not self._face_auth.enabled or self._auth.is_authenticated():
            return False
        self._status.set(token, "authenticating", "本人確認中")
        result = self._face_auth.verify()
        if result.authenticated:
            self._auth.mark_activity()
            self.stop_warning()
            self._status.set(token, "ready", "待機中")
            return True
        detail = result.message
        if result.score is not None:
            detail = f"{detail} スコア={result.score}"
        self._report_face_auth_failure(source, detail, getattr(result, "image_path", ""))
        return False

    def announce_required(self) -> None:
        """起動後に未認証なら本人確認を促す。"""
        if self._auth.enabled and not self._auth.is_authenticated():
            self._status.force_resting()
            self._dashboard_state.add_error_notification("本人確認", "本人確認をしてください。")
            self._speak_status("本人確認をしてください。")
            self._start_warning_timer(self._settings.auth_warning_delay_seconds)

    def start_status_monitor(self) -> None:
        """認証期限切れを監視して待機表示をロック表示へ戻す。"""
        if not self._auth.enabled:
            return
        thread = threading.Thread(target=self._run_status_monitor, daemon=True)
        thread.start()

    def play_lock_warning(self) -> None:
        """本人確認ロック中の警告音を非同期に再生する。"""
        if self._settings.dry_run or not self._settings.auth_warning_sound_enabled:
            return
        try:
            tone = build_auth_warning_tone(self._settings.voicevox_sample_rate)
            threading.Thread(target=self._audio.play_wav, args=(tone,), daemon=True).start()
        except Exception as exc:
            log.exception("本人確認ロック警告音の再生に失敗しました")
            self._report_error("本人確認警告音", exc)

    def stop_warning(self) -> None:
        """本人確認完了時に警告音タイマーを止める。"""
        self._warning_stop.set()
        if self._warning_thread is not None and self._warning_thread is not threading.current_thread():
            self._warning_thread.join(timeout=2)

    def _run_status_monitor(self) -> None:
        """一定間隔で認証状態を画面表示へ反映する。"""
        while not self._shutdown.wait(1.0):
            self._refresh_status()

    def _refresh_status(self) -> None:
        """待機中に認証が切れていたらロック表示へ切り替える。"""
        if not self._auth.enabled or self._auth.is_authenticated():
            return
        self._status.note_idle_waiting()

    def _report_face_auth_failure(self, source: str, detail: str, image_path: str = "") -> None:
        """顔認証失敗を、撮影画像があれば画像付き通知として表示する。"""
        image_url = ""
        if image_path:
            try:
                source_path = Path(image_path)
                if source_path.exists():
                    snapshot_path = Path(self._settings.camera_snapshot_path).expanduser()
                    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source_path, snapshot_path)
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
        self._status.mark_alert()
        self._dashboard_state.add_error_notification("警戒", message)
        self._start_warning_timer(0, force_alert=True)
        result = self._security_alert.dispatch(source, message, image_path)
        if result.executed and not result.succeeded:
            self._dashboard_state.add_error_notification("警戒通知", result.message)

    def _start_warning_timer(self, delay_seconds: float, force_alert: bool = False) -> None:
        """未認証が続いた場合に警告音を繰り返すタイマーを開始する。"""
        if self._settings.dry_run or not self._settings.auth_warning_sound_enabled:
            return
        if self._warning_thread is not None and self._warning_thread.is_alive():
            return
        self._warning_stop.clear()
        self._warning_thread = threading.Thread(
            target=self._run_warning,
            args=(delay_seconds, force_alert),
            daemon=True,
        )
        self._warning_thread.start()

    def _run_warning(self, delay_seconds: float, force_alert: bool) -> None:
        """本人確認が終わるまで警告音を繰り返す。"""
        if self._warning_stop.wait(max(0.0, delay_seconds)):
            return
        started_at = time.monotonic()
        alert_announced = False
        while not self._warning_stop.is_set() and not self._auth.is_authenticated():
            if self._is_recording():
                if self._warning_stop.wait(0.2):
                    return
                continue
            alert_mode = force_alert or time.monotonic() - started_at + delay_seconds >= self._settings.auth_alert_delay_seconds
            if alert_mode:
                self._status.set_alert_mode(True)
                text = "警戒モードに入りました。本人確認してください。" if not alert_announced else "警戒モードです。本人確認してください。"
                alert_announced = True
            else:
                text = "本人確認してください。"
            self._status.note_idle_waiting()
            # 直前で本人確認が完了していれば、警告音と警告発話は行わない。
            if self._warning_stop.is_set() or self._auth.is_authenticated():
                return
            self._play_warning_sound()
            self._speak_status(text)
            if self._warning_stop.wait(self._settings.auth_warning_interval_seconds):
                return

    def _play_warning_sound(self) -> None:
        """本人確認失敗時の警告音を鳴らす。"""
        if self._settings.dry_run or not self._settings.auth_warning_sound_enabled:
            return
        try:
            self._audio.play_wav(build_auth_warning_tone(self._settings.voicevox_sample_rate))
        except Exception as exc:
            log.exception("本人確認警告音の再生に失敗しました")
            self._report_error("本人確認警告音", exc)
