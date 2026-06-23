import json
import logging

from argos.config import AgentSlot, Settings
from argos.core.app import ArgosApp, CodexProgressAnnouncer
from argos.services.auth import hash_keyword


class FakeTimer:
    """テスト内で任意に発火できるタイマー。"""

    timers = []

    def __init__(self, _interval, callback):
        """コールバックを保存する。"""
        self.callback = callback
        self.cancelled = False
        FakeTimer.timers.append(self)

    def start(self):
        """実タイマーのstart互換メソッド。"""
        return None

    def cancel(self):
        """タイマーをキャンセル済みにする。"""
        self.cancelled = True


def _settings():
    return Settings(
        agent_provider="codex",
        agent_state_path="~/.argos/agent-sessions.json",
        stt_gateway_url="http://stt",
        stt_language="ja",
        stt_gateway_token="",
        tts_filter_url="http://filter",
        tts_filter_token="token",
        tts_delimiters="。！？!?",
        voicevox_url="http://voicevox",
        voicevox_speaker=2,
        voicevox_sample_rate=48000,
        voicevox_speed_scale=1.0,
        audio_input_device="in",
        audio_output_device="out",
        audio_output_card="",
        audio_output_volume=90,
        audio_state_path="",
        audio_sample_rate=16000,
        lcd_enabled=False,
        lcd_width=76,
        lcd_height=284,
        lcd_x_offset=82,
        lcd_y_offset=18,
        lcd_dc_pin="D25",
        lcd_cs_pin="D5",
        lcd_reset_pin="D24",
        lcd_baudrate=4_000_000,
        lcd_font_path="",
        lcd_font_size=16,
        dashboard_enabled=False,
        dashboard_host="127.0.0.1",
        dashboard_port=8765,
        dashboard_token="",
        ptt_gpio=17,
        silence_rms_threshold=10,
        dry_run=True,
        agent_slots=(AgentSlot("作業", "codex", "/tmp"),),
        codex_home="",
        codex_model="",
        codex_sandbox="workspace-write",
        codex_bypass_sandbox=False,
        codex_approval_policy="on-request",
        codex_extra_args=(),
        antigravity_command="/home/yuki/.local/bin/agy",
        antigravity_home="~/.gemini/antigravity-cli",
        antigravity_extra_args=(),
        greeting_enabled=False,
        greeting_state_path="",
        tts_cache_enabled=False,
    )


class FakeRecorder:
    def __init__(self, *args):
        self.started = False
        self.cancelled = False
        self.recording = False

    @property
    def is_recording(self):
        return self.recording

    def start(self):
        self.started = True
        self.recording = True

    def stop(self):
        self.recording = False
        return "/tmp/u.wav"

    def cancel(self):
        self.cancelled = True
        self.recording = False


class FakeAudio:
    def __init__(self, *args):
        self.cancelled = False
        self.played = []
        self.volume = args[2]

    def cancel(self):
        self.cancelled = True

    def play_wav(self, wav):
        self.played.append(wav)

    @property
    def is_playing(self):
        return False

    def set_volume(self, volume):
        self.volume = max(0, min(100, int(volume)))
        return self.volume


class FakeStt:
    def __init__(self, *args):
        pass

    def transcribe(self, wav):
        return "こんにちは"


class FakeLocalStt:
    def __init__(self, *args):
        pass

    def transcribe(self, wav):
        return "ローカル認識"


class FakeCodex:
    def __init__(self, *args):
        self.current_name = "作業"
        self.current_provider = "codex"
        self.asked = []

    def ask(self, text):
        self.asked.append(text)
        return "応答"

    def ask_stream(self, text):
        self.asked.append(text)
        yield "応答"

    def next_slot(self):
        self.current_name = "次"
        self.current_provider = "antigravity"
        return "次"

    def reset_current(self):
        self.reset = True


class FakeFilter:
    def __init__(self, *args):
        pass

    def normalize(self, text):
        return f"正規化:{text}"


class FakeVoicevox:
    def __init__(self, *args):
        self.calls = []
        pass

    def synthesize(self, text, speaker=None):
        self.calls.append((text, speaker))
        return text.encode()


class FakeKokoro:
    def __init__(self, *args):
        pass

    def synthesize(self, text):
        return f"kokoro:{text}".encode()


def _patch_app(monkeypatch):
    monkeypatch.setattr("argos.core.app.Recorder", FakeRecorder)
    monkeypatch.setattr("argos.core.app.AudioPlayer", FakeAudio)
    monkeypatch.setattr("argos.core.app.SttGatewayClient", FakeStt)
    monkeypatch.setattr("argos.core.app.FasterWhisperClient", FakeLocalStt)
    monkeypatch.setattr("argos.core.app.create_agent_client", FakeCodex)
    monkeypatch.setattr("argos.core.app.TtsFilterClient", FakeFilter)
    monkeypatch.setattr("argos.core.app.VoicevoxClient", FakeVoicevox)
    monkeypatch.setattr("argos.core.app.KokoroClient", FakeKokoro)


def test_handle_text_dry_run(monkeypatch, capsys):
    _patch_app(monkeypatch)
    monkeypatch.setattr("argos.core.app.random.choice", lambda phrases: phrases[0])
    app = ArgosApp(_settings())

    app._handle_text("依頼")

    assert app._agent.asked == ["依頼"]
    snapshot = app._dashboard_state.snapshot()
    assert [message["role"] for message in snapshot["messages"]] == ["user", "assistant"]
    assert snapshot["messages"][1]["text"] == "応答"
    assert snapshot["status"]["code"] == "ready"
    assert snapshot["status"]["label"] == "待機中"
    output = capsys.readouterr().out
    assert "わかった。少し待ね" in output or "わかった。少し待ってね" in output
    assert "ARGOS> 応答" in output


def test_handle_text_agent_rate_limit_error(monkeypatch, capsys):
    """エージェントがレートリミットエラーを返した際に音声で報告されることをテストする。"""
    _patch_app(monkeypatch)

    class FakeCodexWithError:
        def __init__(self, settings):
            self.current_name = "作業"
            self.current_provider = "codex"

        def ask_stream(self, text: str):
            raise RuntimeError("Rate Limit Exceeded")

    monkeypatch.setattr("argos.core.app.create_agent_client", FakeCodexWithError)
    app = ArgosApp(_settings())

    app._handle_text("依頼")

    output = capsys.readouterr().out
    assert "リミット制限に達しました" in output


def test_handle_text_agent_general_error(monkeypatch, capsys):
    """エージェントが一般的なエラーを返した際に音声で報告されることをテストする。"""
    _patch_app(monkeypatch)

    class FakeCodexWithError:
        def __init__(self, settings):
            self.current_name = "作業"
            self.current_provider = "codex"

        def ask_stream(self, text: str):
            raise RuntimeError("Connection Timeout")

    monkeypatch.setattr("argos.core.app.create_agent_client", FakeCodexWithError)
    app = ArgosApp(_settings())

    app._handle_text("依頼")

    output = capsys.readouterr().out
    assert "エージェントの応答取得に失敗しました" in output


def test_dashboard_shows_current_agent_slot(monkeypatch):
    """起動時の現在スロットをダッシュボード状態へ反映する。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())

    snapshot = app._dashboard_state.snapshot()

    assert snapshot["agent"]["name"] == "作業"
    assert snapshot["agent"]["provider"] == "codex"


def test_deliver_runner_result_adds_slot_message_and_notification(monkeypatch):
    """Runnerで完了した未配信応答をスロット履歴へ反映する。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())

    app._deliver_runner_result("job-1", "作業", "codex", "Runner応答")

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["messages"][-1]["text"] == "Runner応答"
    assert snapshot["notifications"][-1]["title"] == "作業 応答完了"
    assert app._pending_slot_speech["codex\0作業"] == "Runner応答"


def test_deliver_runner_error_adds_notification(monkeypatch):
    """Runnerで失敗した未配信ジョブを通知へ反映する。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())

    app._deliver_runner_error("job-1", "作業", "codex", "失敗しました")

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["notifications"][-1]["source"] == "作業 Runner"
    assert snapshot["notifications"][-1]["priority"] == "high"


def test_double_click_updates_agent_and_clears_listening_status(monkeypatch):
    """スロット切替後は現在スロット表示を更新し、録音中表示を残さない。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    app._dashboard_state.set_status("listening", "録音中")

    app._on_double_click()

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["agent"]["name"] == "次"
    assert snapshot["agent"]["provider"] == "antigravity"
    assert snapshot["status"]["code"] == "ready"
    assert snapshot["status"]["label"] == "待機中"


def test_cancel_clears_listening_status(monkeypatch):
    """短押しキャンセル後は録音中表示を待機中へ戻す。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    app._dashboard_state.set_status("listening", "録音中")

    app._on_cancel()

    snapshot = app._dashboard_state.snapshot()
    assert app._recorder.cancelled is True
    assert snapshot["status"]["code"] == "ready"


def test_locked_ptt_shows_auth_recording_status(monkeypatch):
    """ロック中のPTT録音は本人確認用の録音表示にする。"""
    _patch_app(monkeypatch)
    settings = Settings(
        **{
            **_settings().__dict__,
            "auth_enabled": True,
            "auth_keyword_hash": hash_keyword("解除"),
        }
    )
    app = ArgosApp(settings)

    app._on_ptt_press()
    snapshot = app._dashboard_state.snapshot()
    assert app._recorder.started is True
    assert snapshot["status"]["code"] == "auth_listening"
    assert snapshot["status"]["label"] == "本人確認録音中"

    app._on_ptt_release()
    snapshot = app._dashboard_state.snapshot()
    assert snapshot["status"]["code"] == "authenticating"
    assert snapshot["status"]["label"] == "本人確認中"


def test_locked_double_click_switches_slot_without_auth_release(monkeypatch):
    """ロック中のPTTダブルクリックは本人確認処理へ流さずスロットだけ切り替える。"""
    _patch_app(monkeypatch)
    times = iter([1.00, 1.10, 1.30, 1.40])
    monkeypatch.setattr("argos.hardware.button.time.monotonic", lambda: next(times))
    FakeTimer.timers = []
    monkeypatch.setattr("argos.hardware.button.Timer", FakeTimer)
    settings = Settings(
        **{
            **_settings().__dict__,
            "auth_enabled": True,
            "auth_keyword_hash": hash_keyword("解除"),
        }
    )
    app = ArgosApp(settings)

    app._button.handle_press()
    app._button.handle_release()
    app._button.handle_press()
    app._button.handle_release()

    snapshot = app._dashboard_state.snapshot()
    assert FakeTimer.timers[-1].cancelled is True
    assert app._recorder.cancelled is True
    assert app._worker is None
    assert snapshot["agent"]["name"] == "次"
    assert snapshot["status"]["code"] == "locked"


def test_dashboard_control_updates_mute_state(monkeypatch):
    """ダッシュボード操作でミュート状態を切り替える。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())

    assert app._handle_dashboard_control({"action": "mute"}) == {"muted": True, "volume": 90}
    snapshot = app._dashboard_state.snapshot()
    assert app._audio.cancelled is True
    assert snapshot["audio"]["muted"] is True
    assert snapshot["status"]["code"] == "ready"

    assert app._handle_dashboard_control({"action": "unmute"}) == {"muted": False, "volume": 90}
    snapshot = app._dashboard_state.snapshot()
    assert snapshot["audio"]["muted"] is False
    assert snapshot["status"]["code"] == "ready"


def test_dashboard_control_updates_audio_volume(monkeypatch):
    """ダッシュボード操作で読み上げ音量を変更する。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())

    assert app._handle_dashboard_control({"action": "set_volume", "volume": 42}) == {"muted": False, "volume": 42}
    snapshot = app._dashboard_state.snapshot()

    assert app._audio.volume == 42
    assert snapshot["audio"]["volume"] == 42


def test_dashboard_control_resets_current_agent_session(monkeypatch):
    """ダッシュボード操作で現在スロットのエージェントセッションをリセットする。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())

    result = app._handle_dashboard_control({"action": "reset_agent_session"})
    snapshot = app._dashboard_state.snapshot()

    assert result == {
        "muted": False,
        "volume": 90,
        "session_reset": True,
        "slot": {"name": "作業", "provider": "codex"},
    }
    assert app._agent.reset is True
    assert snapshot["notifications"][0]["title"] == "セッションリセット"
    assert snapshot["notifications"][0]["source"] == "ARGOS"


def test_dashboard_notification_event_can_speak(monkeypatch, capsys):
    """外部通知イベントを読み上げ通知として処理できる。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())

    app._announce_dashboard_notification({"type": "notification", "title": "予定", "text": "旅費申請", "speak": True})

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["display_activity"]["sequence"] == 1
    output = capsys.readouterr().out
    assert "予定。旅費申請" in output


def test_app_restores_audio_state(monkeypatch, tmp_path):
    """起動時に保存済みの音量とミュート状態を復元する。"""
    _patch_app(monkeypatch)
    state_path = tmp_path / "audio-state.json"
    state_path.write_text('{"volume": 37, "muted": true}', encoding="utf-8")
    settings = Settings(**{**_settings().__dict__, "audio_state_path": str(state_path)})

    app = ArgosApp(settings)
    snapshot = app._dashboard_state.snapshot()

    assert app._audio.volume == 37
    assert app._is_muted() is True
    assert snapshot["audio"]["volume"] == 37
    assert snapshot["audio"]["muted"] is True


def test_dashboard_audio_controls_persist_state(monkeypatch, tmp_path):
    """ダッシュボード操作で変更した音量とミュート状態を保存する。"""
    _patch_app(monkeypatch)
    state_path = tmp_path / "audio-state.json"
    settings = Settings(**{**_settings().__dict__, "audio_state_path": str(state_path)})
    app = ArgosApp(settings)

    app._handle_dashboard_control({"action": "set_volume", "volume": 42})
    app._handle_dashboard_control({"action": "mute"})

    assert json.loads(state_path.read_text(encoding="utf-8")) == {"volume": 42, "muted": True}


def test_mute_does_not_override_listening_status(monkeypatch):
    """ミュート状態は録音中表示を上書きしない。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    app._dashboard_state.set_status("listening", "録音中")

    app._set_muted(True)

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["audio"]["muted"] is True
    assert snapshot["status"]["code"] == "listening"


def test_process_recording_uses_stt_and_returns_idle(monkeypatch, capsys):
    _patch_app(monkeypatch)
    monkeypatch.setattr("argos.core.app.check_audio_level", lambda wav: 100)
    monkeypatch.setattr("argos.core.app.random.choice", lambda phrases: phrases[0])
    app = ArgosApp(_settings())

    app._process_recording()

    assert app._agent.asked == ["こんにちは"]
    assert app._button.state.value == "idle"
    assert "ARGOS> 応答" in capsys.readouterr().out


def test_process_recording_greets_on_first_interaction(monkeypatch, capsys, tmp_path):
    """起動時ではなく最初の発話処理時に挨拶する。"""
    _patch_app(monkeypatch)
    monkeypatch.setattr("argos.core.app.check_audio_level", lambda wav: 100)
    settings = Settings(
        **{
            **_settings().__dict__,
            "greeting_enabled": True,
            "greeting_state_path": str(tmp_path / "greeting.json"),
        }
    )
    app = ArgosApp(settings)

    app._process_recording()

    output = capsys.readouterr().out
    assert any(greeting in output for greeting in ("おはよう。", "こんにちは。", "こんばんは。"))


def test_locked_recording_does_not_reach_codex(monkeypatch):
    """未認証の発話はCodexへ送らない。"""
    _patch_app(monkeypatch)
    monkeypatch.setattr("argos.core.app.check_audio_level", lambda wav: 100)
    settings = Settings(
        **{
            **_settings().__dict__,
            "auth_enabled": True,
            "auth_keyword_hash": hash_keyword("解除"),
        }
    )
    app = ArgosApp(settings)
    app._stt.transcribe = lambda _wav: "違う言葉"

    app._process_recording()

    snapshot = app._dashboard_state.snapshot()
    assert app._agent.asked == []
    assert snapshot["status"]["code"] == "locked"
    assert snapshot["notifications"][0]["title"] == "本人確認 エラー"


def test_empty_transcript_adds_notification(monkeypatch):
    """文字起こし結果が空の場合はログ確認用に通知を残す。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    monkeypatch.setattr("argos.core.app.check_audio_level", lambda _wav: 100)
    app._stt.transcribe = lambda _wav: ""
    app._local_stt.transcribe = lambda _wav: ""

    app._process_recording()

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["notifications"][0]["title"] == "文字起こし エラー"
    assert snapshot["notifications"][0]["text"] == "音声を認識できませんでした。"


def test_startup_auth_prompt_is_spoken_when_locked(monkeypatch, capsys):
    """起動後に未認証なら本人確認を促す。"""
    _patch_app(monkeypatch)
    settings = Settings(
        **{
            **_settings().__dict__,
            "auth_enabled": True,
            "auth_keyword_hash": hash_keyword("解除"),
        }
    )
    app = ArgosApp(settings)

    app._announce_auth_required()

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["status"]["code"] == "locked"
    assert snapshot["status"]["label"] == "ロック中"
    assert "本人確認をしてください。" in capsys.readouterr().out


def test_auth_expiry_updates_ready_dashboard_to_locked(monkeypatch):
    """待機中に認証が切れたらロック中表示へ戻す。"""
    _patch_app(monkeypatch)
    settings = Settings(
        **{
            **_settings().__dict__,
            "auth_enabled": True,
            "auth_keyword_hash": hash_keyword("解除"),
        }
    )
    app = ArgosApp(settings)
    app._auth.verify_keyword("解除")
    app._dashboard_state.set_status("ready", "待機中")

    app._auth.lock()
    app._refresh_auth_status()

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["status"]["code"] == "locked"
    assert snapshot["status"]["label"] == "ロック中"


def test_run_initializes_gpio_before_auth_prompt(monkeypatch):
    """本人確認案内の読み上げ前にPTT入力を初期化する。"""
    _patch_app(monkeypatch)
    events = []
    settings = Settings(
        **{
            **_settings().__dict__,
            "dry_run": False,
            "startup_sound_enabled": False,
            "startup_splash_seconds": 0,
            "auth_enabled": True,
            "auth_keyword_hash": hash_keyword("解除"),
        }
    )

    def fake_gpio(*_args):
        events.append("gpio")
        return object()

    monkeypatch.setattr("argos.core.app.GpioPttInput", fake_gpio)
    app = ArgosApp(settings)
    original_speak_status = app._speak_status

    def speak_status(text):
        events.append("speak")
        original_speak_status(text)

    def sleep_once(_seconds):
        app._shutdown.set()

    app._speak_status = speak_status
    monkeypatch.setattr("argos.core.app.time.sleep", sleep_once)

    app.run()

    assert events[:2] == ["gpio", "speak"]


def test_auth_warning_repeats_until_authenticated(monkeypatch):
    """未認証が続いたら案内を繰り返し、認証後に止める。"""
    _patch_app(monkeypatch)
    settings = Settings(
        **{
            **_settings().__dict__,
            "dry_run": False,
            "auth_enabled": True,
            "auth_keyword_hash": hash_keyword("解除"),
            "auth_warning_delay_seconds": 0,
            "auth_alert_delay_seconds": 30,
            "auth_warning_interval_seconds": 0.01,
        }
    )
    app = ArgosApp(settings)

    app._start_auth_warning_timer(0)
    import time

    time.sleep(0.03)
    app._auth.verify_keyword("解除")
    app._stop_auth_warning()

    snapshot = app._dashboard_state.snapshot()
    assert app._audio.played
    assert snapshot["status"]["code"] == "locked"


def test_auth_warning_is_suppressed_while_recording(monkeypatch):
    """録音中は本人確認案内を読み上げず、マイクへの回り込みを避ける。"""
    _patch_app(monkeypatch)
    settings = Settings(
        **{
            **_settings().__dict__,
            "dry_run": False,
            "auth_enabled": True,
            "auth_keyword_hash": hash_keyword("解除"),
            "auth_warning_delay_seconds": 0,
            "auth_alert_delay_seconds": 0,
            "auth_warning_interval_seconds": 0.01,
        }
    )
    app = ArgosApp(settings)
    app._recorder.recording = True

    app._start_auth_warning_timer(0)
    import time

    time.sleep(0.03)
    app._stop_auth_warning()

    assert not app._audio.played


def test_auth_warning_enters_alert_mode_after_delay(monkeypatch):
    """警戒モード遅延を超えたら警戒中に切り替える。"""
    _patch_app(monkeypatch)
    settings = Settings(
        **{
            **_settings().__dict__,
            "dry_run": False,
            "auth_enabled": True,
            "auth_keyword_hash": hash_keyword("解除"),
            "auth_warning_delay_seconds": 0,
            "auth_alert_delay_seconds": 0,
            "auth_warning_interval_seconds": 0.05,
        }
    )
    app = ArgosApp(settings)

    app._start_auth_warning_timer(0)
    import time

    time.sleep(0.03)
    app._stop_auth_warning()

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["status"]["code"] == "alert"
    assert snapshot["status"]["label"] == "警戒中"


def test_keyword_unlock_does_not_send_keyword_to_codex(monkeypatch, capsys):
    """音声キーワードは解除だけに使い、Codexへ送らない。"""
    _patch_app(monkeypatch)
    monkeypatch.setattr("argos.core.app.check_audio_level", lambda wav: 100)
    settings = Settings(
        **{
            **_settings().__dict__,
            "auth_enabled": True,
            "auth_keyword_hash": hash_keyword("解除"),
        }
    )
    app = ArgosApp(settings)
    app._stt.transcribe = lambda _wav: "解除"

    app._process_recording()

    assert app._agent.asked == []
    assert "本人確認しました。" in capsys.readouterr().out


def test_authenticated_recording_reaches_codex(monkeypatch):
    """認証済みの発話はCodexへ送る。"""
    _patch_app(monkeypatch)
    monkeypatch.setattr("argos.core.app.check_audio_level", lambda wav: 100)
    settings = Settings(
        **{
            **_settings().__dict__,
            "auth_enabled": True,
            "auth_keyword_hash": hash_keyword("解除"),
        }
    )
    app = ArgosApp(settings)
    app._auth.verify_keyword("解除")

    app._process_recording()

    assert app._agent.asked == ["こんにちは"]


def test_face_auth_success_allows_current_recording(monkeypatch):
    """顔認証が成功した発話はそのままCodexへ送る。"""
    _patch_app(monkeypatch)
    monkeypatch.setattr("argos.core.app.check_audio_level", lambda wav: 100)
    settings = Settings(
        **{
            **_settings().__dict__,
            "auth_enabled": True,
            "auth_keyword_hash": hash_keyword("解除"),
            "auth_face_enabled": True,
        }
    )
    app = ArgosApp(settings)
    app._face_auth.verify = lambda: type(
        "Result",
        (),
        {"authenticated": True, "message": "顔認証しました。", "score": 0},
    )()

    app._process_recording()

    assert app._agent.asked == ["こんにちは"]


def test_face_auth_failure_falls_back_to_keyword(monkeypatch, capsys):
    """顔認証失敗後も音声キーワードで解除できる。"""
    _patch_app(monkeypatch)
    monkeypatch.setattr("argos.core.app.check_audio_level", lambda wav: 100)
    settings = Settings(
        **{
            **_settings().__dict__,
            "auth_enabled": True,
            "auth_keyword_hash": hash_keyword("解除"),
            "auth_face_enabled": True,
        }
    )
    app = ArgosApp(settings)
    app._stt.transcribe = lambda _wav: "解除"
    app._face_auth.verify = lambda: type(
        "Result",
        (),
        {"authenticated": False, "message": "顔認証に失敗しました。", "score": 99},
    )()

    app._process_recording()

    assert app._agent.asked == []
    assert "本人確認しました。" in capsys.readouterr().out


def test_auth_keyword_attempt_logs_transcript(monkeypatch, caplog):
    """本人確認キーワード照合時にSTT結果をログへ出す。"""
    _patch_app(monkeypatch)
    caplog.set_level(logging.INFO, logger="argos.core.app")
    monkeypatch.setattr("argos.core.app.check_audio_level", lambda wav: 100)
    settings = Settings(
        **{
            **_settings().__dict__,
            "auth_enabled": True,
            "auth_keyword_hash": hash_keyword("解除"),
            "auth_face_enabled": False,
        }
    )
    app = ArgosApp(settings)
    app._stt.transcribe = lambda _wav: "会場"

    app._process_recording()

    assert "本人確認キーワード照合" in caplog.text
    assert "会場" in caplog.text
    assert "音声キーワードが一致しません。" in caplog.text


def test_face_auth_failure_notification_has_image(monkeypatch, tmp_path):
    """顔認証失敗時は撮影画像を通知に付ける。"""
    _patch_app(monkeypatch)
    monkeypatch.setattr("argos.core.app.check_audio_level", lambda wav: 100)
    latest_path = tmp_path / "camera-latest.jpg"
    captured_path = tmp_path / "auth-face.jpg"
    captured_path.write_bytes(b"jpg")
    monkeypatch.setattr("argos.core.app.FACE_AUTH_FAILURE_IMAGE_PATH", latest_path)
    settings = Settings(
        **{
            **_settings().__dict__,
            "auth_enabled": True,
            "auth_keyword_hash": hash_keyword("解除"),
            "auth_face_enabled": True,
        }
    )
    app = ArgosApp(settings)
    app._stt.transcribe = lambda _wav: "違う言葉"
    app._face_auth.verify = lambda: type(
        "Result",
        (),
        {
            "authenticated": False,
            "message": "顔認証に失敗しました。",
            "score": 99,
            "image_path": str(captured_path),
        },
    )()

    app._process_recording()

    snapshot = app._dashboard_state.snapshot()
    face_notice = snapshot["notifications"][0]
    assert face_notice["title"] == "顔認証 エラー"
    assert face_notice["image_url"].startswith("/camera/latest.jpg?t=")
    assert latest_path.read_bytes() == b"jpg"


def test_repeated_auth_failure_dispatches_security_alert(monkeypatch):
    """本人確認失敗がしきい値に達したら警戒アクションを呼ぶ。"""
    _patch_app(monkeypatch)
    monkeypatch.setattr("argos.core.app.check_audio_level", lambda wav: 100)
    settings = Settings(
        **{
            **_settings().__dict__,
            "auth_enabled": True,
            "auth_keyword_hash": hash_keyword("解除"),
            "auth_failure_threshold": 1,
        }
    )
    app = ArgosApp(settings)
    calls = []

    def dispatch(source, message, image_path=""):
        """テスト用の警戒通知を記録する。"""
        calls.append((source, message, image_path))
        return type("Result", (), {"executed": True, "succeeded": True, "message": "ok"})()

    app._security_alert.dispatch = dispatch
    app._stt.transcribe = lambda _wav: "違う言葉"

    app._process_recording()

    assert calls == [("本人確認", "本人確認に複数回失敗しました。", "")]


def test_codex_progress_announcer_speaks_start_and_wait(monkeypatch):
    spoken = []
    monkeypatch.setattr("argos.core.app.random.choice", lambda phrases: phrases[0])
    announcer = CodexProgressAnnouncer(
        speak_status=spoken.append,
        first_delay_seconds=0.01,
        interval_seconds=10,
    )

    announcer.start()
    import time

    time.sleep(0.05)
    announcer.stop()

    assert spoken[0] == "わかった。少し待ってね。"
    assert spoken[1] == "ちょっと時間かかってるけど、もう少し待ってね。"


def test_codex_progress_stop_waits_for_current_status(monkeypatch):
    import threading
    import time

    calls = []
    speaking = threading.Event()
    allow_finish = threading.Event()
    monkeypatch.setattr("argos.core.app.random.choice", lambda phrases: phrases[0])

    def speak_status(text):
        calls.append(text)
        if len(calls) == 2:
            speaking.set()
            allow_finish.wait(timeout=1)

    announcer = CodexProgressAnnouncer(
        speak_status=speak_status,
        first_delay_seconds=0.01,
        interval_seconds=10,
    )

    announcer.start()
    assert speaking.wait(timeout=1)
    stopper = threading.Thread(target=announcer.stop)
    stopper.start()
    time.sleep(0.03)

    assert stopper.is_alive()
    allow_finish.set()
    stopper.join(timeout=1)
    assert not stopper.is_alive()


def test_codex_progress_can_be_disabled(monkeypatch):
    _patch_app(monkeypatch)
    settings = Settings(**{**_settings().__dict__, "codex_progress_voice": False})
    app = ArgosApp(settings)

    assert app._start_codex_progress() is None


def test_ptt_and_status_methods(monkeypatch, capsys):
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())

    app._on_ptt_press()
    app._on_cancel()
    app._on_double_click()

    assert app._recorder.started
    assert app._recorder.cancelled
    assert app._audio.cancelled
    assert "次に切り替えました" in capsys.readouterr().out


def test_status_message_is_shown_on_lcd(monkeypatch, capsys):
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    shown = []
    app._lcd = type("FakeLcd", (), {"show_text": lambda self, text: shown.append(text)})()

    app._speak_status("表示テスト")

    assert shown == ["表示テスト"]
    assert "表示テスト" in capsys.readouterr().out


def test_busy_button_press_cancels_audio_and_starts_recording(monkeypatch):
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())

    app._button.mark_busy()
    app._button.handle_press()

    assert app._audio.cancelled
    assert app._recorder.started
    assert app._button.state.value == "listening"


def test_stream_response_splits_tts_chunks(monkeypatch):
    _patch_app(monkeypatch)
    settings = Settings(**{**_settings().__dict__, "dry_run": False})
    app = ArgosApp(settings)

    response = app._speak_response_stream(["一文目。二文", "目です。\n三文目"])

    assert response == "一文目。二文目です。\n三文目"
    assert app._audio.played == [
        "正規化:一文目。".encode(),
        "正規化:二文目です。".encode(),
        "正規化:三文目".encode(),
    ]
    assert app._dashboard_state.snapshot()["display_activity"]["sequence"] == 3


def test_stream_response_discards_queued_chunks_after_cancel(monkeypatch):
    _patch_app(monkeypatch)
    settings = Settings(**{**_settings().__dict__, "dry_run": False})
    app = ArgosApp(settings)
    played = []

    def cancel_on_first_play(wav):
        played.append(wav)
        app._on_cancel()

    app._audio.play_wav = cancel_on_first_play

    response = app._speak_response_stream(["一文目。二文目。三文目。"])

    assert response == "一文目。二文目。三文目。"
    assert played == ["正規化:一文目。".encode()]


def test_cancel_during_stream_keeps_background_slot_response(monkeypatch):
    """スロット切替時の短押しキャンセル後も、元スロットの応答取得は継続する。"""
    _patch_app(monkeypatch)
    settings = Settings(**{**_settings().__dict__, "dry_run": False})
    app = ArgosApp(settings)

    def ask_stream(_text):
        yield "前半。"
        app._on_cancel()
        app._on_double_click()
        yield "後半。"

    app._agent.ask_stream = ask_stream

    app._handle_text("依頼")

    snapshot = app._dashboard_state.snapshot()
    slots = {slot["name"]: slot for slot in snapshot["slots"]}
    assert slots["作業"]["unread"] is True
    assert app._pending_slot_speech["codex\0作業"] == "前半。後半。"
    app._dashboard_state.set_agent("作業", "codex")
    assert [message["text"] for message in app._dashboard_state.snapshot()["messages"]] == ["依頼", "前半。後半。"]


def test_pending_slot_response_uses_chunked_cancelable_tts(monkeypatch):
    """未読応答の読み上げも通常応答と同じチャンク分割TTSを使う。"""
    _patch_app(monkeypatch)
    settings = Settings(**{**_settings().__dict__, "dry_run": False})
    app = ArgosApp(settings)
    app._pending_slot_speech["codex\0作業"] = "一文目。二文目です。"

    app._start_pending_slot_response()
    assert app._pending_speech_thread is not None
    app._pending_speech_thread.join(timeout=1)

    assert not app._pending_speech_thread.is_alive()
    assert app._audio.played == [
        "正規化:一文目。".encode(),
        "正規化:二文目です。".encode(),
    ]


def test_stream_response_waits_while_muted(monkeypatch):
    """ミュート中はTTS再生を待機し、解除後に読み上げる。"""
    import threading
    import time

    _patch_app(monkeypatch)
    settings = Settings(**{**_settings().__dict__, "dry_run": False})
    app = ArgosApp(settings)
    app._set_muted(True)

    worker = threading.Thread(target=lambda: app._speak_response_stream(["一文目。"]), daemon=True)
    worker.start()
    time.sleep(0.05)

    assert app._audio.played == []
    assert worker.is_alive()

    app._set_muted(False)
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert app._audio.played == ["正規化:一文目。".encode()]


def test_speak_response_plays_normalized_voice(monkeypatch):
    """単発応答を正規化して読み上げる。"""
    _patch_app(monkeypatch)
    settings = Settings(**{**_settings().__dict__, "dry_run": False})
    app = ArgosApp(settings)

    app._speak_response("返答")

    assert app._audio.played == ["正規化:返答".encode()]


def test_speak_response_wakes_dashboard_display(monkeypatch):
    """読み上げ開始時にスクリーンセーバー解除用の表示アクティビティを更新する。"""
    _patch_app(monkeypatch)
    settings = Settings(**{**_settings().__dict__, "dry_run": False})
    app = ArgosApp(settings)

    app._speak_response("返答")

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["display_activity"]["sequence"] == 1


def test_status_speech_wakes_dashboard_display(monkeypatch):
    """状態通知の読み上げ開始時にも表示アクティビティを更新する。"""
    _patch_app(monkeypatch)
    settings = Settings(**{**_settings().__dict__, "dry_run": False})
    app = ArgosApp(settings)

    app._speak_status("確認中")

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["display_activity"]["sequence"] == 1


def test_speak_response_uses_current_slot_voicevox_speaker(monkeypatch):
    """現在スロットに設定したVOICEVOX話者で読み上げる。"""
    _patch_app(monkeypatch)
    settings = Settings(
        **{
            **_settings().__dict__,
            "dry_run": False,
            "agent_slots": (AgentSlot("作業", "codex", "/tmp", voicevox_speaker=8),),
        }
    )
    app = ArgosApp(settings)

    app._speak_response("返答")

    assert app._voicevox.calls == [("正規化:返答", 8)]


def test_speak_response_uses_kokoro_when_voicevox_url_is_empty(monkeypatch):
    """VOICEVOX URLが空ならKokoroで読み上げる。"""
    _patch_app(monkeypatch)
    settings = Settings(**{**_settings().__dict__, "dry_run": False, "voicevox_url": ""})
    app = ArgosApp(settings)

    app._speak_response("返答")

    assert app._audio.played == ["kokoro:正規化:返答".encode()]


def test_speak_response_falls_back_to_kokoro_on_voicevox_error(monkeypatch):
    """VOICEVOX障害時はKokoroで読み上げを継続する。"""
    _patch_app(monkeypatch)
    settings = Settings(**{**_settings().__dict__, "dry_run": False})
    app = ArgosApp(settings)
    app._voicevox.synthesize = lambda _text, speaker=None: (_ for _ in ()).throw(RuntimeError("接続できません"))

    app._speak_response("返答")

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["notifications"][0]["title"] == "VOICEVOX エラー"
    assert app._audio.played == ["kokoro:正規化:返答".encode()]


def test_stream_voicevox_error_is_shown_on_dashboard(monkeypatch):
    """本文読み上げ中のVOICEVOX障害を画面通知で確認できる。"""
    _patch_app(monkeypatch)
    settings = Settings(**{**_settings().__dict__, "dry_run": False})
    app = ArgosApp(settings)
    app._voicevox.synthesize = lambda _text, speaker=None: (_ for _ in ()).throw(RuntimeError("接続できません"))

    app._speak_response_stream(["読み上げ。"])

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["status"]["code"] == "error"
    assert snapshot["notifications"][0]["title"] == "VOICEVOX エラー"


def test_speak_response_audio_error_is_shown_on_dashboard(monkeypatch):
    """音声再生障害を画面通知で確認できる。"""
    _patch_app(monkeypatch)
    settings = Settings(**{**_settings().__dict__, "dry_run": False})
    app = ArgosApp(settings)
    app._audio.play_wav = lambda _wav: (_ for _ in ()).throw(RuntimeError("再生できません"))

    app._speak_response("返答")

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["notifications"][0]["title"] == "音声再生 エラー"


def test_status_voicevox_error_is_shown_on_dashboard(monkeypatch):
    """VOICEVOX障害を画面通知で確認できる。"""
    _patch_app(monkeypatch)
    settings = Settings(**{**_settings().__dict__, "dry_run": False})
    app = ArgosApp(settings)
    app._voicevox.synthesize = lambda _text, speaker=None: (_ for _ in ()).throw(RuntimeError("接続できません"))

    app._speak_status("確認中")

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["status"]["code"] == "error"
    assert snapshot["notifications"][0]["title"] == "VOICEVOX エラー"
    assert snapshot["notifications"][0]["text"] == "接続できません"


def test_stt_error_is_shown_on_dashboard(monkeypatch):
    """文字起こし障害を画面通知で確認できる。"""
    _patch_app(monkeypatch)
    monkeypatch.setattr("argos.core.app.check_audio_level", lambda _wav: 100)
    app = ArgosApp(_settings())
    app._stt.transcribe = lambda _wav: (_ for _ in ()).throw(RuntimeError("応答なし"))
    app._local_stt.transcribe = lambda _wav: (_ for _ in ()).throw(RuntimeError("ローカル失敗"))

    app._process_recording()

    snapshot = app._dashboard_state.snapshot()
    assert [notification["title"] for notification in snapshot["notifications"]] == [
        "stt-gateway エラー",
        "文字起こし エラー",
    ]
    assert snapshot["notifications"][1]["text"] == "ローカル失敗"


def test_stt_falls_back_to_local_whisper_on_gateway_error(monkeypatch):
    """stt-gateway障害時はfaster-whisperで文字起こしを継続する。"""
    _patch_app(monkeypatch)
    monkeypatch.setattr("argos.core.app.check_audio_level", lambda _wav: 100)
    app = ArgosApp(_settings())
    app._stt.transcribe = lambda _wav: (_ for _ in ()).throw(RuntimeError("応答なし"))

    app._process_recording()

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["notifications"][0]["title"] == "stt-gateway エラー"
    assert app._agent.asked == ["ローカル認識"]


def test_stt_uses_local_whisper_when_gateway_url_is_empty(monkeypatch):
    """STTゲートウェイURLが空ならfaster-whisperで文字起こしする。"""
    _patch_app(monkeypatch)
    monkeypatch.setattr("argos.core.app.check_audio_level", lambda _wav: 100)
    settings = Settings(**{**_settings().__dict__, "stt_gateway_url": ""})
    app = ArgosApp(settings)

    app._process_recording()

    assert app._agent.asked == ["ローカル認識"]


def test_tts_cache_integration(monkeypatch):
    """TTSキャッシュ有効時、同じ短文の2回目の合成はキャッシュを使う。"""
    import tempfile

    _patch_app(monkeypatch)
    with tempfile.TemporaryDirectory() as tmpdir:
        settings = Settings(
            **{
                **_settings().__dict__,
                "tts_cache_enabled": True,
                "tts_cache_dir": tmpdir,
                "tts_cache_max_chars": 30,
            }
        )
        app = ArgosApp(settings)

        text = "キャッシュテスト"
        wav_data_1 = app._synthesize_tts(text)
        assert wav_data_1 == text.encode()
        assert len(app._voicevox.calls) == 1
        assert app._voicevox.calls[0] == (text, 2)

        wav_data_2 = app._synthesize_tts(text)
        assert wav_data_2 == wav_data_1
        assert len(app._voicevox.calls) == 1
