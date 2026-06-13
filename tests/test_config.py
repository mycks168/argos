from argos.config import load_settings


def test_load_default_slot(monkeypatch):
    monkeypatch.delenv("ARGOS_AGENT_SLOT_1", raising=False)
    monkeypatch.delenv("ARGOS_CODEX_SLOT_1", raising=False)
    monkeypatch.setenv("ARGOS_AGENT_CWD", "/tmp/work")
    monkeypatch.setenv("ARGOS_AGENT_SLOT_NAME", "作業")

    settings = load_settings()

    assert settings.agent_slots[0].name == "作業"
    assert settings.agent_slots[0].provider == "codex"
    assert settings.agent_slots[0].cwd == "/tmp/work"


def test_load_agent_provider(monkeypatch):
    """LLMエージェントプロバイダーを読み込む。"""
    monkeypatch.setenv("ARGOS_AGENT_PROVIDER", "codex")

    settings = load_settings()

    assert settings.agent_provider == "codex"


def test_load_default_slot_uses_pi_home(monkeypatch):
    monkeypatch.delenv("ARGOS_AGENT_SLOT_1", raising=False)
    monkeypatch.delenv("ARGOS_CODEX_SLOT_1", raising=False)
    monkeypatch.delenv("ARGOS_AGENT_CWD", raising=False)
    monkeypatch.delenv("ARGOS_CODEX_CWD", raising=False)
    monkeypatch.setenv("ARGOS_AGENT_SLOT_NAME", "作業")

    settings = load_settings()

    assert settings.agent_slots[0].cwd == "/home/pi"


def test_load_numbered_slots(monkeypatch):
    monkeypatch.setenv("ARGOS_AGENT_SLOT_1", "一番,codex,/tmp/a")
    monkeypatch.setenv("ARGOS_AGENT_SLOT_2", "二番,antigravity,/tmp/b")
    monkeypatch.delenv("ARGOS_AGENT_SLOT_3", raising=False)

    settings = load_settings()

    assert [slot.name for slot in settings.agent_slots] == ["一番", "二番"]
    assert settings.agent_slots[0].provider == "codex"
    assert settings.agent_slots[1].provider == "antigravity"
    assert settings.agent_slots[1].cwd == "/tmp/b"


def test_load_numbered_slots_with_voicevox_speaker(monkeypatch):
    """スロットごとのVOICEVOX話者IDを読み込む。"""
    monkeypatch.setenv("ARGOS_AGENT_SLOT_1", "一番,codex,/tmp/a,8")
    monkeypatch.setenv("ARGOS_AGENT_SLOT_2", "二番,antigravity,/tmp/b,14")
    monkeypatch.delenv("ARGOS_AGENT_SLOT_3", raising=False)

    settings = load_settings()

    assert settings.agent_slots[0].voicevox_speaker == 8
    assert settings.agent_slots[1].voicevox_speaker == 14


def test_load_default_slot_voicevox_speaker(monkeypatch):
    """単一既定スロットのVOICEVOX話者IDを読み込む。"""
    monkeypatch.delenv("ARGOS_AGENT_SLOT_1", raising=False)
    monkeypatch.delenv("ARGOS_CODEX_SLOT_1", raising=False)
    monkeypatch.setenv("ARGOS_AGENT_SLOT_VOICEVOX_SPEAKER", "7")

    settings = load_settings()

    assert settings.agent_slots[0].voicevox_speaker == 7


def test_load_legacy_codex_slots(monkeypatch):
    """旧ARGOS_CODEX_SLOT形式を互換読み込みする。"""
    monkeypatch.delenv("ARGOS_AGENT_SLOT_1", raising=False)
    monkeypatch.setenv("ARGOS_CODEX_SLOT_1", "一番,/tmp/a,/tmp/home-a,gpt-5")
    monkeypatch.setenv("ARGOS_CODEX_SLOT_2", "二番,/tmp/b,,")
    monkeypatch.delenv("ARGOS_CODEX_SLOT_3", raising=False)

    settings = load_settings()

    assert [slot.name for slot in settings.agent_slots] == ["一番", "二番"]
    assert [slot.provider for slot in settings.agent_slots] == ["codex", "codex"]
    assert settings.agent_slots[0].cwd == "/tmp/a"
    assert settings.agent_slots[1].cwd == "/tmp/b"


def test_load_tts_delimiters(monkeypatch):
    monkeypatch.setenv("ARGOS_TTS_DELIMITERS", "。！？、")

    settings = load_settings()

    assert settings.tts_delimiters == "。！？、"


def test_load_stt_gateway_token(monkeypatch):
    """STTゲートウェイのBearerトークンを読み込む。"""
    monkeypatch.setenv("STT_GATEWAY_BEARER_TOKEN", "stt-token")

    settings = load_settings()

    assert settings.stt_gateway_token == "stt-token"


def test_load_voicevox_speed_scale(monkeypatch):
    """VOICEVOXの話速設定を読み込む。"""
    monkeypatch.setenv("VOICEVOX_SPEED_SCALE", "1.1")

    settings = load_settings()

    assert settings.voicevox_speed_scale == 1.1


def test_load_audio_input_devices(monkeypatch):
    """複数の録音デバイス候補を読み込む。"""
    monkeypatch.setenv("AUDIO_INPUT_DEVICES", "plughw:CARD=One,DEV=0; plughw:CARD=Two,DEV=0")

    settings = load_settings()

    assert settings.audio_input_devices == ("plughw:CARD=One,DEV=0", "plughw:CARD=Two,DEV=0")


def test_load_audio_state_path(monkeypatch):
    """音量とミュート状態の保存先を読み込む。"""
    monkeypatch.setenv("ARGOS_AUDIO_STATE_PATH", "/tmp/audio-state.json")

    settings = load_settings()

    assert settings.audio_state_path == "/tmp/audio-state.json"


def test_load_argos_input_devices_from_comma_text(monkeypatch):
    """ARGOS_INPUT_DEVICESでもALSAデバイス文字列を壊さず読み込む。"""
    monkeypatch.delenv("AUDIO_INPUT_DEVICES", raising=False)
    monkeypatch.delenv("ARGOS_AUDIO_INPUT_DEVICES", raising=False)
    monkeypatch.setenv("ARGOS_INPUT_DEVICES", "plughw:CARD=One,DEV=0, plughw:CARD=Two,DEV=0")

    settings = load_settings()

    assert settings.audio_input_devices == ("plughw:CARD=One,DEV=0", "plughw:CARD=Two,DEV=0")


def test_load_kokoro_settings(monkeypatch):
    """Kokoroフォールバック設定を読み込む。"""
    monkeypatch.setenv("ARGOS_KOKORO_VOICE", "jf_alpha")
    monkeypatch.setenv("ARGOS_KOKORO_SPEED", "1.2")
    monkeypatch.setenv("ARGOS_KOKORO_REPO_ID", "repo")
    monkeypatch.setenv("ARGOS_KOKORO_SAMPLE_RATE", "24000")

    settings = load_settings()

    assert settings.kokoro_voice == "jf_alpha"
    assert settings.kokoro_speed == 1.2
    assert settings.kokoro_repo_id == "repo"
    assert settings.kokoro_sample_rate == 24000


def test_load_whisper_settings(monkeypatch):
    """faster-whisperフォールバック設定を読み込む。"""
    monkeypatch.setenv("ARGOS_WHISPER_MODEL_SIZE", "small")
    monkeypatch.setenv("ARGOS_WHISPER_DEVICE", "cpu")
    monkeypatch.setenv("ARGOS_WHISPER_COMPUTE_TYPE", "int8")

    settings = load_settings()

    assert settings.whisper_model_size == "small"
    assert settings.whisper_device == "cpu"
    assert settings.whisper_compute_type == "int8"


def test_load_antigravity_settings(monkeypatch):
    """Antigravity CLI設定を読み込む。"""
    monkeypatch.setenv("ARGOS_ANTIGRAVITY_COMMAND", "/tmp/agy")
    monkeypatch.setenv("ARGOS_ANTIGRAVITY_HOME", "/tmp/ag-home")
    monkeypatch.setenv("ARGOS_ANTIGRAVITY_EXTRA_ARGS", "--x --y")
    monkeypatch.setenv("ARGOS_ANTIGRAVITY_SKIP_PERMISSIONS", "true")
    monkeypatch.setenv("ARGOS_ANTIGRAVITY_SANDBOX", "true")
    monkeypatch.setenv("ARGOS_ANTIGRAVITY_PRINT_TIMEOUT", "30s")
    monkeypatch.setenv("ARGOS_ANTIGRAVITY_CONTINUE_SESSION", "true")
    monkeypatch.setenv("ARGOS_ACKNOWLEDGEMENT_URL", "http://ack")
    monkeypatch.setenv("ARGOS_ACKNOWLEDGEMENT_TOKEN", "ack-token")

    settings = load_settings()

    assert settings.antigravity_command == "/tmp/agy"
    assert settings.antigravity_home == "/tmp/ag-home"
    assert settings.antigravity_extra_args == ("--x", "--y")
    assert settings.antigravity_skip_permissions is True
    assert settings.antigravity_sandbox is True
    assert settings.antigravity_print_timeout == "30s"
    assert settings.antigravity_continue_session is True
    assert settings.acknowledgement_url == "http://ack"
    assert settings.acknowledgement_token == "ack-token"


def test_load_hermes_settings(monkeypatch):
    """Hermes Agent CLI設定を読み込む。"""
    monkeypatch.setenv("ARGOS_HERMES_COMMAND", "/tmp/hermes")
    monkeypatch.setenv("ARGOS_HERMES_MODEL", "model-a")
    monkeypatch.setenv("ARGOS_HERMES_PROVIDER", "provider-a")
    monkeypatch.setenv("ARGOS_HERMES_TOOLSETS", "tools-a")
    monkeypatch.setenv("ARGOS_HERMES_SKILLS", "skills-a")
    monkeypatch.setenv("ARGOS_HERMES_SOURCE", "argos-test")
    monkeypatch.setenv("ARGOS_HERMES_PASS_SESSION_ID", "false")
    monkeypatch.setenv("ARGOS_HERMES_RESUME_SAVED", "false")
    monkeypatch.setenv("ARGOS_HERMES_EXTRA_ARGS", "--x --y")

    settings = load_settings()

    assert settings.hermes_command == "/tmp/hermes"
    assert settings.hermes_model == "model-a"
    assert settings.hermes_provider == "provider-a"
    assert settings.hermes_toolsets == "tools-a"
    assert settings.hermes_skills == "skills-a"
    assert settings.hermes_source == "argos-test"
    assert settings.hermes_pass_session_id is False
    assert settings.hermes_resume_saved is False
    assert settings.hermes_extra_args == ("--x", "--y")


def test_load_codex_progress_settings(monkeypatch):
    monkeypatch.setenv("ARGOS_CODEX_PROGRESS_VOICE", "false")
    monkeypatch.setenv("ARGOS_CODEX_PROGRESS_FIRST_DELAY_SECONDS", "3")
    monkeypatch.setenv("ARGOS_CODEX_PROGRESS_INTERVAL_SECONDS", "7")

    settings = load_settings()

    assert settings.codex_progress_voice is False
    assert settings.codex_progress_first_delay_seconds == 3
    assert settings.codex_progress_interval_seconds == 7


def test_load_greeting_settings(monkeypatch):
    """発話時挨拶の設定を読み込む。"""
    monkeypatch.setenv("ARGOS_GREETING_ENABLED", "false")
    monkeypatch.setenv("ARGOS_GREETING_STATE_PATH", "/tmp/greeting.json")

    settings = load_settings()

    assert settings.greeting_enabled is False
    assert settings.greeting_state_path == "/tmp/greeting.json"


def test_load_startup_settings(monkeypatch):
    """起動演出の設定を読み込む。"""
    monkeypatch.setenv("ARGOS_STARTUP_SPLASH_ENABLED", "false")
    monkeypatch.setenv("ARGOS_STARTUP_SPLASH_SECONDS", "1.5")
    monkeypatch.setenv("ARGOS_STARTUP_SOUND_ENABLED", "false")

    settings = load_settings()

    assert settings.startup_splash_enabled is False
    assert settings.startup_splash_seconds == 1.5
    assert settings.startup_sound_enabled is False


def test_load_auth_settings(monkeypatch):
    """本人確認の設定を読み込む。"""
    monkeypatch.setenv("ARGOS_AUTH_ENABLED", "true")
    monkeypatch.setenv("ARGOS_AUTH_KEYWORD_HASH", "hash")
    monkeypatch.setenv("ARGOS_AUTH_TRUST_SECONDS", "60")
    monkeypatch.setenv("ARGOS_AUTH_FAILURE_THRESHOLD", "2")
    monkeypatch.setenv("ARGOS_AUTH_FACE_ENABLED", "true")
    monkeypatch.setenv("ARGOS_AUTH_FACE_SAMPLES_DIR", "/tmp/faces")
    monkeypatch.setenv("ARGOS_AUTH_FACE_CAPTURE_COMMAND", "echo {path}")
    monkeypatch.setenv("ARGOS_AUTH_FACE_CAPTURE_PATH", "/tmp/face.jpg")
    monkeypatch.setenv("ARGOS_AUTH_FACE_IMAGE_ROTATION", "90")
    monkeypatch.setenv("ARGOS_AUTH_FACE_THRESHOLD", "12")
    monkeypatch.setenv("ARGOS_AUTH_FACE_MIN_MATCHES", "2")
    monkeypatch.setenv("ARGOS_AUTH_FACE_DETECTION_ENABLED", "false")
    monkeypatch.setenv("ARGOS_AUTH_FACE_MIN_DETECTED_FACES", "1")
    monkeypatch.setenv("ARGOS_AUTH_FACE_MAX_DETECTED_FACES", "2")
    monkeypatch.setenv("ARGOS_AUTH_FACE_DETECTOR_MODEL_PATH", "/tmp/yunet.onnx")
    monkeypatch.setenv("ARGOS_AUTH_FACE_RECOGNIZER_MODEL_PATH", "/tmp/sface.onnx")
    monkeypatch.setenv("ARGOS_AUTH_FACE_SFACE_THRESHOLD", "0.5")
    monkeypatch.setenv("ARGOS_AUTH_ALERT_COMMAND", "echo alert")
    monkeypatch.setenv("ARGOS_AUTH_WARNING_SOUND_ENABLED", "false")
    monkeypatch.setenv("ARGOS_AUTH_WARNING_DELAY_SECONDS", "4")
    monkeypatch.setenv("ARGOS_AUTH_ALERT_DELAY_SECONDS", "30")
    monkeypatch.setenv("ARGOS_AUTH_WARNING_INTERVAL_SECONDS", "0.5")

    settings = load_settings()

    assert settings.auth_enabled is True
    assert settings.auth_keyword_hash == "hash"
    assert settings.auth_trust_seconds == 60
    assert settings.auth_failure_threshold == 2
    assert settings.auth_face_enabled is True
    assert settings.auth_face_samples_dir == "/tmp/faces"
    assert settings.auth_face_capture_command == "echo {path}"
    assert settings.auth_face_capture_path == "/tmp/face.jpg"
    assert settings.auth_face_image_rotation == 90
    assert settings.auth_face_threshold == 12
    assert settings.auth_face_min_matches == 2
    assert settings.auth_face_detection_enabled is False
    assert settings.auth_face_min_detected_faces == 1
    assert settings.auth_face_max_detected_faces == 2
    assert settings.auth_face_detector_model_path == "/tmp/yunet.onnx"
    assert settings.auth_face_recognizer_model_path == "/tmp/sface.onnx"
    assert settings.auth_face_sface_threshold == 0.5
    assert settings.auth_alert_command == "echo alert"
    assert settings.auth_warning_sound_enabled is False
    assert settings.auth_warning_delay_seconds == 4
    assert settings.auth_alert_delay_seconds == 30
    assert settings.auth_warning_interval_seconds == 0.5


def test_load_codex_bypass_sandbox(monkeypatch):
    monkeypatch.setenv("ARGOS_CODEX_BYPASS_SANDBOX", "true")

    settings = load_settings()

    assert settings.codex_bypass_sandbox is True


def test_load_lcd_settings(monkeypatch):
    monkeypatch.setenv("ARGOS_LCD_ENABLED", "true")
    monkeypatch.setenv("ARGOS_LCD_WIDTH", "76")
    monkeypatch.setenv("ARGOS_LCD_HEIGHT", "284")
    monkeypatch.setenv("ARGOS_LCD_X_OFFSET", "82")
    monkeypatch.setenv("ARGOS_LCD_Y_OFFSET", "18")
    monkeypatch.setenv("ARGOS_LCD_FONT_PATH", "/tmp/ipag.ttf")

    settings = load_settings()

    assert settings.lcd_enabled is True
    assert settings.lcd_width == 76
    assert settings.lcd_height == 284
    assert settings.lcd_x_offset == 82
    assert settings.lcd_y_offset == 18
    assert settings.lcd_font_path == "/tmp/ipag.ttf"


def test_load_dashboard_settings(monkeypatch):
    monkeypatch.setenv("ARGOS_DASHBOARD_ENABLED", "true")
    monkeypatch.setenv("ARGOS_DASHBOARD_HOST", "0.0.0.0")
    monkeypatch.setenv("ARGOS_DASHBOARD_PORT", "9876")
    monkeypatch.setenv("ARGOS_DASHBOARD_TOKEN", "secret")
    monkeypatch.setenv("ARGOS_DASHBOARD_SCREENSAVER_SECONDS", "12.5")
    monkeypatch.setenv("ARGOS_LOCATION_PROVIDER", "remote")
    monkeypatch.setenv("ARGOS_REMOTE_LOCATION_URL", "http://example.test/gps")
    monkeypatch.setenv("ARGOS_REMOTE_LOCATION_TIMEOUT_SECONDS", "1.5")

    settings = load_settings()

    assert settings.dashboard_enabled is True
    assert settings.dashboard_host == "0.0.0.0"
    assert settings.dashboard_port == 9876
    assert settings.dashboard_token == "secret"
    assert settings.dashboard_screensaver_seconds == 12.5
    assert settings.location_provider == "remote"
    assert settings.remote_location_url == "http://example.test/gps"
    assert settings.remote_location_timeout_seconds == 1.5
