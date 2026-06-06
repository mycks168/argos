from argos.config import AgentSlot, Settings
from argos.core.app import ArgosApp, CodexProgressAnnouncer
from argos.services.auth import hash_keyword


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
    )


class FakeRecorder:
    def __init__(self, *args):
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def stop(self):
        return "/tmp/u.wav"

    def cancel(self):
        self.cancelled = True


class FakeAudio:
    def __init__(self, *args):
        self.cancelled = False
        self.played = []

    def cancel(self):
        self.cancelled = True

    def play_wav(self, wav):
        self.played.append(wav)


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
        pass

    def synthesize(self, text):
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
    output = capsys.readouterr().out
    assert "わかった。少し待ってね" in output
    assert "ARGOS> 応答" in output


def test_dashboard_shows_current_agent_slot(monkeypatch):
    """起動時の現在スロットをダッシュボード状態へ反映する。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())

    snapshot = app._dashboard_state.snapshot()

    assert snapshot["agent"]["name"] == "作業"
    assert snapshot["agent"]["provider"] == "codex"


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


def test_dashboard_control_updates_mute_state(monkeypatch):
    """ダッシュボード操作でミュート状態を切り替える。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())

    assert app._handle_dashboard_control("mute") == {"muted": True}
    snapshot = app._dashboard_state.snapshot()
    assert app._audio.cancelled is True
    assert snapshot["audio"]["muted"] is True
    assert snapshot["status"]["code"] == "ready"

    assert app._handle_dashboard_control("unmute") == {"muted": False}
    snapshot = app._dashboard_state.snapshot()
    assert snapshot["audio"]["muted"] is False
    assert snapshot["status"]["code"] == "ready"


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
    app._voicevox.synthesize = lambda _text: (_ for _ in ()).throw(RuntimeError("接続できません"))

    app._speak_response("返答")

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["notifications"][0]["title"] == "VOICEVOX エラー"
    assert app._audio.played == ["kokoro:正規化:返答".encode()]


def test_stream_voicevox_error_is_shown_on_dashboard(monkeypatch):
    """本文読み上げ中のVOICEVOX障害を画面通知で確認できる。"""
    _patch_app(monkeypatch)
    settings = Settings(**{**_settings().__dict__, "dry_run": False})
    app = ArgosApp(settings)
    app._voicevox.synthesize = lambda _text: (_ for _ in ()).throw(RuntimeError("接続できません"))

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
    app._voicevox.synthesize = lambda _text: (_ for _ in ()).throw(RuntimeError("接続できません"))

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
