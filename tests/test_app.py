import json
import logging

from argos.config import AgentSlot, Settings
from argos.core.app import ArgosApp, CodexProgressAnnouncer, _strip_leading_wakeword
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


class FakeThread:
    """テスト中に処理スレッドを実行せず開始だけ記録する。"""

    started = []

    def __init__(self, target=None, args=(), daemon=None, **_kwargs):
        """スレッドの実行対象を保存する。"""
        self.target = target
        self.args = args
        self.daemon = daemon

    def start(self):
        """実処理は行わず、開始されたことだけ記録する。"""
        FakeThread.started.append((self.target, self.args))


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
        antigravity_command="agy",
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
        self.playing = False

    def cancel(self):
        self.cancelled = True

    def play_wav(self, wav):
        self.played.append(wav)

    @property
    def is_playing(self):
        return self.playing

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

    assert app._handle_dashboard_control({"action": "mute"}) == {"muted": True, "volume": 90, "microphone_enabled": True}
    snapshot = app._dashboard_state.snapshot()
    assert app._audio.cancelled is True
    assert snapshot["audio"]["muted"] is True
    assert snapshot["status"]["code"] == "ready"

    assert app._handle_dashboard_control({"action": "unmute"}) == {"muted": False, "volume": 90, "microphone_enabled": True}
    snapshot = app._dashboard_state.snapshot()
    assert snapshot["audio"]["muted"] is False
    assert snapshot["status"]["code"] == "ready"


def test_dashboard_control_updates_audio_volume(monkeypatch):
    """ダッシュボード操作で読み上げ音量を変更する。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())

    assert app._handle_dashboard_control({"action": "set_volume", "volume": 42}) == {
        "muted": False,
        "volume": 42,
        "microphone_enabled": True,
    }
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


def test_dashboard_control_updates_microphone_state(monkeypatch):
    """ダッシュボード操作でマイク受付状態を切り替える。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())

    assert app._handle_dashboard_control({"action": "disable_microphone"}) == {
        "muted": False,
        "volume": 90,
        "microphone_enabled": False,
    }
    snapshot = app._dashboard_state.snapshot()
    assert snapshot["microphone"]["enabled"] is False

    app._on_ptt_press()
    assert app._recorder.started is False

    assert app._on_wakeword_detected() is False

    assert app._handle_dashboard_control({"action": "enable_microphone"}) == {
        "muted": False,
        "volume": 90,
        "microphone_enabled": True,
    }
    assert app._dashboard_state.snapshot()["microphone"]["enabled"] is True


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


def test_process_recording_sends_low_rms_ptt_to_stt(monkeypatch):
    """PTT録音は小さい声でもRMSだけでは破棄しない。"""
    _patch_app(monkeypatch)
    monkeypatch.setattr("argos.core.app.check_audio_level", lambda wav: 69)
    app = ArgosApp(_settings())

    app._process_recording()

    assert app._agent.asked == ["こんにちは"]


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


def test_run_skips_gpio_when_ptt_gpio_is_empty(monkeypatch):
    """PTT GPIO未設定ならGPIO入力を初期化しない。"""
    _patch_app(monkeypatch)
    settings = Settings(
        **{
            **_settings().__dict__,
            "ptt_gpio": None,
            "dry_run": False,
            "startup_sound_enabled": False,
            "startup_splash_seconds": 0,
            "auth_enabled": False,
        }
    )
    called = []

    def fake_gpio(*_args):
        called.append("gpio")
        return object()

    def sleep_once(_seconds):
        app._shutdown.set()

    monkeypatch.setattr("argos.core.app.GpioPttInput", fake_gpio)
    monkeypatch.setattr("argos.core.app.time.sleep", sleep_once)
    app = ArgosApp(settings)

    app.run()

    assert called == []


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


def test_wakeword_listener_starts_when_enabled(monkeypatch):
    """ウェイクワード有効時だけ監視サービスを開始する。"""
    _patch_app(monkeypatch)
    started = []

    class FakeWakeWordListener:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            started.append(self.kwargs)

    monkeypatch.setattr("argos.core.app.WakeWordListener", FakeWakeWordListener)
    settings = Settings(
        **{
            **_settings().__dict__,
            "dry_run": False,
            "wakeword_enabled": True,
            "wakeword_model_dir": "/tmp/wakeword",
            "wakeword_threshold": 0.6,
            "wakeword_capture_sample_rate": 48000,
            "wakeword_pre_roll_seconds": 2.5,
            "wakeword_min_actual_seconds": 0.3,
            "wakeword_endpoint_mode": "vad",
            "wakeword_vad_model_path": "/tmp/silero.onnx",
            "wakeword_vad_threshold": 0.4,
            "wakeword_vad_min_silence_seconds": 1.2,
            "wakeword_vad_check_seconds": 0.2,
            "wakeword_score_log_path": "/tmp/argos/wakeword-score.log",
        }
    )
    app = ArgosApp(settings)

    app._start_wakeword_listener()

    assert started
    assert started[0]["model_dir"] == "/tmp/wakeword"
    assert started[0]["threshold"] == 0.6
    assert started[0]["capture_sample_rate"] == 48000
    assert started[0]["pre_roll_seconds"] == 2.5
    assert started[0]["min_actual_seconds"] == 0.3
    assert started[0]["endpoint_mode"] == "vad"
    assert started[0]["vad_model_path"] == "/tmp/silero.onnx"
    assert started[0]["vad_threshold"] == 0.4
    assert started[0]["vad_min_silence_seconds"] == 1.2
    assert started[0]["vad_check_seconds"] == 0.2
    assert started[0]["score_log_path"] == "/tmp/argos/wakeword-score.log"


def test_wakeword_recording_reuses_existing_processing(monkeypatch, tmp_path):
    """ウェイクワード後のWAVを通常の文字起こし処理へ渡す。"""
    _patch_app(monkeypatch)
    wav_path = tmp_path / "wake.wav"
    wav_path.write_bytes(b"dummy")
    app = ArgosApp(_settings())
    handled = []

    monkeypatch.setattr("argos.core.app.check_audio_level", lambda _path: 100)
    app._transcribe_wav = lambda path: "ウェイクワード入力"
    app._handle_text = lambda text: handled.append(text)

    app._process_wakeword_recording(str(wav_path))

    assert handled == ["ウェイクワード入力"]
    assert not wav_path.exists()


def test_wakeword_recording_sends_low_rms_to_stt(monkeypatch, tmp_path):
    """ウェイクワード後録音もRMSだけでは破棄しない。"""
    _patch_app(monkeypatch)
    wav_path = tmp_path / "wake.wav"
    wav_path.write_bytes(b"dummy")
    app = ArgosApp(_settings())
    handled = []

    monkeypatch.setattr("argos.core.app.check_audio_level", lambda _path: 69)
    app._transcribe_wav = lambda path: "小さい声"
    app._handle_text = lambda text: handled.append(text)

    app._process_wakeword_recording(str(wav_path))

    assert handled == ["小さい声"]
    assert not wav_path.exists()


def test_wakeword_recording_strips_leading_wakeword(monkeypatch, tmp_path):
    """ウェイクワード経由のSTT結果だけ先頭の呼びかけを除去する。"""
    _patch_app(monkeypatch)
    wav_path = tmp_path / "wake.wav"
    wav_path.write_bytes(b"dummy")
    app = ArgosApp(_settings())
    handled = []

    monkeypatch.setattr("argos.core.app.check_audio_level", lambda _path: 100)
    app._transcribe_wav = lambda path: "アルゴス、今日の天気は"
    app._handle_text = lambda text: handled.append(text)

    app._process_wakeword_recording(str(wav_path))

    assert handled == ["今日の天気は"]
    assert not wav_path.exists()


def test_wakeword_recording_requires_stt_wakeword_when_enabled(monkeypatch, tmp_path):
    """設定有効時はSTT結果が呼びかけから始まらない誤検知を破棄する。"""
    _patch_app(monkeypatch)
    wav_path = tmp_path / "wake.wav"
    wav_path.write_bytes(b"dummy")
    settings = Settings(**{**_settings().__dict__, "wakeword_require_stt_wakeword": True})
    app = ArgosApp(settings)
    handled = []

    monkeypatch.setattr("argos.core.app.check_audio_level", lambda _path: 100)
    app._transcribe_wav = lambda path: "テレビの音です"
    app._handle_text = lambda text: handled.append(text)

    app._process_wakeword_recording(str(wav_path))

    assert handled == []
    assert app._dashboard_state.snapshot()["notifications"][0]["title"] == "ウェイクワード"


def test_wakeword_recording_strips_wakeword_before_auth(monkeypatch, tmp_path):
    """本人確認時も先頭のウェイクワードを除去して照合する。"""
    _patch_app(monkeypatch)
    wav_path = tmp_path / "wake.wav"
    wav_path.write_bytes(b"dummy")
    settings = Settings(
        **{
            **_settings().__dict__,
            "auth_enabled": True,
            "auth_keyword_hash": hash_keyword("唐揚げ"),
        }
    )
    app = ArgosApp(settings)

    monkeypatch.setattr("argos.core.app.check_audio_level", lambda _path: 100)
    app._transcribe_wav = lambda path: "アルゴス、唐揚げ"

    app._process_wakeword_recording(str(wav_path))

    assert app._auth.is_authenticated()
    assert not wav_path.exists()


def test_strip_leading_wakeword_keeps_middle_word():
    """文中のアルゴスは削らず、先頭の呼びかけだけ削る。"""
    assert _strip_leading_wakeword("アルゴス、唐揚げ") == "唐揚げ"
    assert _strip_leading_wakeword("アルコス 今日の天気は") == "今日の天気は"
    assert _strip_leading_wakeword("今日はアルゴスの話") == "今日はアルゴスの話"


def test_wakeword_recording_ignores_empty_transcript(monkeypatch, tmp_path):
    """ウェイクワード後の文字起こしが空ならエージェントへ投げない。"""
    _patch_app(monkeypatch)
    wav_path = tmp_path / "wake.wav"
    wav_path.write_bytes(b"dummy")
    app = ArgosApp(_settings())
    handled = []

    monkeypatch.setattr("argos.core.app.check_audio_level", lambda _path: 100)
    app._transcribe_wav = lambda path: ""
    app._handle_text = lambda text: handled.append(text)

    app._process_wakeword_recording(str(wav_path))

    assert handled == []
    assert not wav_path.exists()


def test_wakeword_detected_is_ignored_while_ptt_recording(monkeypatch):
    """PTT録音中のウェイクワード検知は二重録音を避けるため無視する。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    app._recorder.recording = True
    app._dashboard_state.set_status("listening", "録音中")

    accepted = app._on_wakeword_detected()

    assert accepted is False
    assert app._dashboard_state.snapshot()["status"]["code"] == "listening"


def test_wakeword_detected_is_ignored_while_speaking(monkeypatch):
    """読み上げ中のウェイクワード検知は自己音声の誤検知として無視する。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    app._audio.playing = True
    app._dashboard_state.set_status("speaking", "読み上げ中")

    accepted = app._on_wakeword_detected()

    assert accepted is False
    assert app._dashboard_state.snapshot()["status"]["code"] == "speaking"


def test_wakeword_detected_is_ignored_while_thinking(monkeypatch):
    """考え中のウェイクワード誤検知は処理中のTTSキャンセル世代を進めない。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    app._dashboard_state.set_status("thinking", "考え中")
    generation = app._current_cancel_generation()

    accepted = app._on_wakeword_detected()

    assert accepted is False
    assert app._current_cancel_generation() == generation
    assert app._dashboard_state.snapshot()["status"]["code"] == "thinking"
    assert app._audio.cancelled is False


def test_wakeword_detected_is_ignored_after_tts_cooldown(monkeypatch):
    """読み上げ直後は自己音声対策としてウェイクワードだけ無視する。"""
    _patch_app(monkeypatch)
    settings = Settings(**{**_settings().__dict__, "wakeword_tts_cooldown_seconds": 2.0})
    app = ArgosApp(settings)
    app._mark_tts_finished()

    accepted = app._on_wakeword_detected()

    assert accepted is False
    assert app._dashboard_state.snapshot()["status"]["code"] == "ready"


def test_ptt_still_records_during_wakeword_cooldown(monkeypatch):
    """ウェイクワードのクールダウン中でもPTT録音は開始できる。"""
    _patch_app(monkeypatch)
    settings = Settings(**{**_settings().__dict__, "wakeword_tts_cooldown_seconds": 2.0})
    app = ArgosApp(settings)
    app._mark_tts_finished()

    app._on_ptt_press()

    assert app._recorder.started
    assert app._dashboard_state.snapshot()["status"]["code"] == "listening"


def test_ptt_release_sets_transcribing_status(monkeypatch):
    """通常状態のPTT解放後は文字起こし中の専用ステータスへ切り替える。"""
    _patch_app(monkeypatch)
    FakeThread.started = []
    monkeypatch.setattr("argos.core.app.threading.Thread", FakeThread)
    app = ArgosApp(_settings())

    app._on_ptt_press()
    app._on_ptt_release()

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["status"]["code"] == "transcribing"
    assert snapshot["status"]["label"] == "文字起こし中"
    assert FakeThread.started == [(app._process_recording, ())]


def test_ptt_press_extends_wakeword_recording(monkeypatch):
    """ウェイクワード録音中のPTTは別録音を開始せず発話継続扱いにする。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    app._wakeword_recording.set()

    app._on_ptt_press()

    assert app._wakeword_ptt_hold.is_set()
    assert not app._recorder.started
    assert app._dashboard_state.snapshot()["status"]["code"] == "listening"

    app._on_ptt_release()

    assert not app._wakeword_ptt_hold.is_set()
    assert app._worker is None


def test_wakeword_recording_ready_sets_transcribing_status(monkeypatch, tmp_path):
    """ウェイクワード録音完了後も文字起こし中の専用ステータスへ切り替える。"""
    _patch_app(monkeypatch)
    FakeThread.started = []
    monkeypatch.setattr("argos.core.app.threading.Thread", FakeThread)
    wav_path = tmp_path / "wake.wav"
    wav_path.write_bytes(b"dummy")
    app = ArgosApp(_settings())

    app._on_wakeword_recording_ready(str(wav_path))

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["status"]["code"] == "transcribing"
    assert snapshot["status"]["label"] == "文字起こし中"
    assert FakeThread.started == [(app._process_wakeword_recording, (str(wav_path),))]


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
    assert app._dashboard_state.snapshot()["status"]["code"] == "listening"


def test_interrupted_response_does_not_clear_new_recording_status(monkeypatch):
    """読み上げ中にPTT録音へ入った場合、古い処理終了で録音中表示を消さない。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())

    def speak_response_stream(_deltas, dashboard_message_id="", slot_key=""):
        app._on_ptt_press()
        return "応答"

    app._speak_response_stream = speak_response_stream

    app._handle_text("依頼")

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["status"]["code"] == "listening"
    assert snapshot["status"]["label"] == "録音中"
    assert app._recorder.started


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
