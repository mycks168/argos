from argos.config import load_settings


def test_load_default_slot(monkeypatch):
    monkeypatch.delenv("ARGOS_CODEX_SLOT_1", raising=False)
    monkeypatch.setenv("ARGOS_CODEX_CWD", "/tmp/work")
    monkeypatch.setenv("ARGOS_CODEX_SLOT_NAME", "作業")

    settings = load_settings()

    assert settings.codex_slots[0].name == "作業"
    assert settings.codex_slots[0].cwd == "/tmp/work"


def test_load_default_slot_uses_pi_home(monkeypatch):
    monkeypatch.delenv("ARGOS_CODEX_SLOT_1", raising=False)
    monkeypatch.delenv("ARGOS_CODEX_CWD", raising=False)
    monkeypatch.setenv("ARGOS_CODEX_SLOT_NAME", "作業")

    settings = load_settings()

    assert settings.codex_slots[0].cwd == "/home/pi"


def test_load_numbered_slots(monkeypatch):
    monkeypatch.setenv("ARGOS_CODEX_SLOT_1", "一番,/tmp/a,/tmp/home-a,gpt-5")
    monkeypatch.setenv("ARGOS_CODEX_SLOT_2", "二番,/tmp/b,,")
    monkeypatch.delenv("ARGOS_CODEX_SLOT_3", raising=False)

    settings = load_settings()

    assert [slot.name for slot in settings.codex_slots] == ["一番", "二番"]
    assert settings.codex_slots[0].codex_home == "/tmp/home-a"
    assert settings.codex_slots[1].cwd == "/tmp/b"


def test_load_tts_delimiters(monkeypatch):
    monkeypatch.setenv("ARGOS_TTS_DELIMITERS", "。！？、")

    settings = load_settings()

    assert settings.tts_delimiters == "。！？、"


def test_load_voicevox_speed_scale(monkeypatch):
    """VOICEVOXの話速設定を読み込む。"""
    monkeypatch.setenv("VOICEVOX_SPEED_SCALE", "1.1")

    settings = load_settings()

    assert settings.voicevox_speed_scale == 1.1


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

    settings = load_settings()

    assert settings.dashboard_enabled is True
    assert settings.dashboard_host == "0.0.0.0"
    assert settings.dashboard_port == 9876
    assert settings.dashboard_token == "secret"
