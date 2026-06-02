from argos.config import CodexSlot, Settings
from argos.core.app import ArgosApp, CodexProgressAnnouncer


def _settings():
    return Settings(
        stt_gateway_url="http://stt",
        stt_language="ja",
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
        codex_slots=(CodexSlot("作業", "/tmp", "", ""),),
        codex_sandbox="workspace-write",
        codex_bypass_sandbox=False,
        codex_approval_policy="on-request",
        codex_extra_args=(),
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


class FakeCodex:
    def __init__(self, *args):
        self.current_name = "作業"
        self.asked = []

    def ask(self, text):
        self.asked.append(text)
        return "応答"

    def ask_stream(self, text):
        self.asked.append(text)
        yield "応答"

    def next_slot(self):
        self.current_name = "次"
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


def _patch_app(monkeypatch):
    monkeypatch.setattr("argos.core.app.Recorder", FakeRecorder)
    monkeypatch.setattr("argos.core.app.AudioPlayer", FakeAudio)
    monkeypatch.setattr("argos.core.app.SttGatewayClient", FakeStt)
    monkeypatch.setattr("argos.core.app.CodexCliClient", FakeCodex)
    monkeypatch.setattr("argos.core.app.TtsFilterClient", FakeFilter)
    monkeypatch.setattr("argos.core.app.VoicevoxClient", FakeVoicevox)


def test_handle_text_dry_run(monkeypatch, capsys):
    _patch_app(monkeypatch)
    monkeypatch.setattr("argos.core.app.random.choice", lambda phrases: phrases[0])
    app = ArgosApp(_settings())

    app._handle_text("依頼")

    assert app._codex.asked == ["依頼"]
    snapshot = app._dashboard_state.snapshot()
    assert [message["role"] for message in snapshot["messages"]] == ["user", "assistant"]
    assert snapshot["messages"][1]["text"] == "応答"
    output = capsys.readouterr().out
    assert "わかった。少し待ってね" in output
    assert "ARGOS> 応答" in output


def test_process_recording_uses_stt_and_returns_idle(monkeypatch, capsys):
    _patch_app(monkeypatch)
    monkeypatch.setattr("argos.core.app.check_audio_level", lambda wav: 100)
    monkeypatch.setattr("argos.core.app.random.choice", lambda phrases: phrases[0])
    app = ArgosApp(_settings())

    app._process_recording()

    assert app._codex.asked == ["こんにちは"]
    assert app._button.state.value == "idle"
    assert "ARGOS> 応答" in capsys.readouterr().out


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


def test_speak_response_plays_normalized_voice(monkeypatch):
    """単発応答を正規化して読み上げる。"""
    _patch_app(monkeypatch)
    settings = Settings(**{**_settings().__dict__, "dry_run": False})
    app = ArgosApp(settings)

    app._speak_response("返答")

    assert app._audio.played == ["正規化:返答".encode()]


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

    app._process_recording()

    snapshot = app._dashboard_state.snapshot()
    assert snapshot["notifications"][0]["title"] == "文字起こし エラー"
    assert snapshot["notifications"][0]["text"] == "応答なし"
