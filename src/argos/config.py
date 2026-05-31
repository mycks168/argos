"""ARGOS の設定読み込み。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _bool_env(name: str, default: bool) -> bool:
    """環境変数を真偽値として読み込む。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class CodexSlot:
    """Codex の会話スロット設定。"""

    name: str
    cwd: str
    codex_home: str
    model: str


@dataclass(frozen=True)
class Settings:
    """アプリ全体の設定値。"""

    stt_gateway_url: str
    stt_language: str
    tts_filter_url: str
    tts_filter_token: str
    tts_delimiters: str
    voicevox_url: str
    voicevox_speaker: int
    voicevox_sample_rate: int
    voicevox_speed_scale: float
    audio_input_device: str
    audio_output_device: str
    audio_output_card: str
    audio_output_volume: int
    audio_sample_rate: int
    lcd_enabled: bool
    lcd_width: int
    lcd_height: int
    lcd_x_offset: int
    lcd_y_offset: int
    lcd_dc_pin: str
    lcd_cs_pin: str
    lcd_reset_pin: str
    lcd_baudrate: int
    lcd_font_path: str
    lcd_font_size: int
    dashboard_enabled: bool
    dashboard_host: str
    dashboard_port: int
    dashboard_token: str
    ptt_gpio: int
    silence_rms_threshold: float
    dry_run: bool
    codex_slots: tuple[CodexSlot, ...]
    codex_sandbox: str
    codex_bypass_sandbox: bool
    codex_approval_policy: str
    codex_extra_args: tuple[str, ...]
    codex_progress_voice: bool = True
    codex_progress_first_delay_seconds: float = 8.0
    codex_progress_interval_seconds: float = 20.0


def _load_codex_slots() -> tuple[CodexSlot, ...]:
    """環境変数から Codex 会話スロットを読み込む。"""
    slots: list[CodexSlot] = []
    default_cwd = os.environ.get("ARGOS_CODEX_CWD", "/home/pi")
    default_home = os.environ.get("ARGOS_CODEX_HOME", "")
    default_model = os.environ.get("ARGOS_CODEX_MODEL", "")
    index = 1
    while True:
        raw = os.environ.get(f"ARGOS_CODEX_SLOT_{index}", "")
        if not raw:
            break
        parts = [part.strip() for part in raw.split(",", 3)]
        if parts and parts[0]:
            slots.append(
                CodexSlot(
                    name=parts[0],
                    cwd=parts[1] if len(parts) > 1 and parts[1] else default_cwd,
                    codex_home=parts[2] if len(parts) > 2 and parts[2] else default_home,
                    model=parts[3] if len(parts) > 3 and parts[3] else default_model,
                )
            )
        index += 1
    if slots:
        return tuple(slots)
    return (
        CodexSlot(
            name=os.environ.get("ARGOS_CODEX_SLOT_NAME", "デフォルト"),
            cwd=default_cwd,
            codex_home=default_home,
            model=default_model,
        ),
    )


def load_settings() -> Settings:
    """環境変数と .env から設定を構築する。"""
    extra_args = tuple(arg for arg in os.environ.get("ARGOS_CODEX_EXTRA_ARGS", "").split() if arg)
    return Settings(
        stt_gateway_url=os.environ.get("STT_GATEWAY_URL", "http://localhost:23000"),
        stt_language=os.environ.get("STT_GATEWAY_LANGUAGE", "ja"),
        tts_filter_url=os.environ.get("TTS_FILTER_URL", ""),
        tts_filter_token=os.environ.get("TTS_FILTER_BEARER_TOKEN", ""),
        tts_delimiters=os.environ.get("ARGOS_TTS_DELIMITERS", "。！？!?"),
        voicevox_url=os.environ.get("VOICEVOX_URL", "http://localhost:50021"),
        voicevox_speaker=int(os.environ.get("VOICEVOX_SPEAKER", "2")),
        voicevox_sample_rate=int(os.environ.get("VOICEVOX_SAMPLE_RATE", "48000")),
        voicevox_speed_scale=float(os.environ.get("VOICEVOX_SPEED_SCALE", "1.0")),
        audio_input_device=os.environ.get("AUDIO_DEVICE", "plughw:CARD=Microphone,DEV=0"),
        audio_output_device=os.environ.get("AUDIO_OUTPUT_DEVICE", "default"),
        audio_output_card=os.environ.get("AUDIO_OUTPUT_CARD", ""),
        audio_output_volume=int(os.environ.get("AUDIO_OUTPUT_VOLUME", "90")),
        audio_sample_rate=int(os.environ.get("AUDIO_SAMPLE_RATE", "16000")),
        lcd_enabled=_bool_env("ARGOS_LCD_ENABLED", False),
        lcd_width=int(os.environ.get("ARGOS_LCD_WIDTH", "76")),
        lcd_height=int(os.environ.get("ARGOS_LCD_HEIGHT", "284")),
        lcd_x_offset=int(os.environ.get("ARGOS_LCD_X_OFFSET", "82")),
        lcd_y_offset=int(os.environ.get("ARGOS_LCD_Y_OFFSET", "18")),
        lcd_dc_pin=os.environ.get("ARGOS_LCD_DC_PIN", "D25"),
        lcd_cs_pin=os.environ.get("ARGOS_LCD_CS_PIN", "D5"),
        lcd_reset_pin=os.environ.get("ARGOS_LCD_RESET_PIN", "D24"),
        lcd_baudrate=int(os.environ.get("ARGOS_LCD_BAUDRATE", "4000000")),
        lcd_font_path=os.environ.get("ARGOS_LCD_FONT_PATH", ""),
        lcd_font_size=int(os.environ.get("ARGOS_LCD_FONT_SIZE", "16")),
        dashboard_enabled=_bool_env("ARGOS_DASHBOARD_ENABLED", False),
        dashboard_host=os.environ.get("ARGOS_DASHBOARD_HOST", "127.0.0.1"),
        dashboard_port=int(os.environ.get("ARGOS_DASHBOARD_PORT", "8765")),
        dashboard_token=os.environ.get("ARGOS_DASHBOARD_TOKEN", ""),
        ptt_gpio=int(os.environ.get("ARGOS_PTT_GPIO", os.environ.get("PI3_PTT_GPIO", "17"))),
        silence_rms_threshold=float(os.environ.get("SILENCE_RMS_THRESHOLD", "200")),
        dry_run=_bool_env("DRY_RUN", False),
        codex_slots=_load_codex_slots(),
        codex_sandbox=os.environ.get("ARGOS_CODEX_SANDBOX", "workspace-write"),
        codex_bypass_sandbox=_bool_env("ARGOS_CODEX_BYPASS_SANDBOX", False),
        codex_approval_policy=os.environ.get("ARGOS_CODEX_APPROVAL", "on-request"),
        codex_extra_args=extra_args,
        codex_progress_voice=_bool_env("ARGOS_CODEX_PROGRESS_VOICE", True),
        codex_progress_first_delay_seconds=float(
            os.environ.get("ARGOS_CODEX_PROGRESS_FIRST_DELAY_SECONDS", "8")
        ),
        codex_progress_interval_seconds=float(
            os.environ.get("ARGOS_CODEX_PROGRESS_INTERVAL_SECONDS", "20")
        ),
    )


def ensure_runtime_dirs() -> None:
    """ARGOS が使う実行時ディレクトリを作成する。"""
    Path("/tmp/argos").mkdir(parents=True, exist_ok=True)
