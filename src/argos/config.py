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
class AgentSlot:
    """LLMエージェントの会話スロット設定。"""

    name: str
    provider: str
    cwd: str


@dataclass(frozen=True)
class Settings:
    """アプリ全体の設定値。"""

    agent_provider: str
    agent_state_path: str
    agent_slots: tuple[AgentSlot, ...]
    stt_gateway_url: str
    stt_language: str
    stt_gateway_token: str
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
    codex_home: str
    codex_model: str
    codex_sandbox: str
    codex_bypass_sandbox: bool
    codex_approval_policy: str
    codex_extra_args: tuple[str, ...]
    antigravity_command: str
    antigravity_home: str
    antigravity_extra_args: tuple[str, ...]
    antigravity_skip_permissions: bool = False
    antigravity_sandbox: bool = False
    antigravity_print_timeout: str = "5m0s"
    antigravity_continue_session: bool = False
    antigravity_resume_saved: bool = False
    antigravity_prompt_prefix: str = ""
    hermes_command: str = "hermes"
    hermes_model: str = ""
    hermes_provider: str = ""
    hermes_toolsets: str = ""
    hermes_skills: str = ""
    hermes_source: str = "argos"
    hermes_pass_session_id: bool = True
    hermes_resume_saved: bool = True
    hermes_extra_args: tuple[str, ...] = ()
    codex_progress_voice: bool = True
    codex_progress_first_delay_seconds: float = 8.0
    codex_progress_interval_seconds: float = 20.0
    greeting_enabled: bool = True
    greeting_state_path: str = "~/.local/state/argos/greeting-state.json"
    startup_splash_enabled: bool = True
    startup_splash_seconds: float = 3.0
    startup_sound_enabled: bool = True
    auth_enabled: bool = False
    auth_keyword_hash: str = ""
    auth_trust_seconds: int = 1800
    auth_failure_threshold: int = 3
    auth_face_enabled: bool = False
    auth_face_samples_dir: str = "~/.local/share/argos/face-auth"
    auth_face_capture_command: str = "rpicam-still --nopreview --timeout 700 --width 640 --height 480 -o {path}"
    auth_face_capture_path: str = "/tmp/argos/auth-face.jpg"
    auth_face_image_rotation: int = 0
    auth_face_threshold: int = 68
    auth_face_min_matches: int = 1
    auth_face_detection_enabled: bool = True
    auth_face_min_detected_faces: int = 1
    auth_face_max_detected_faces: int = 1
    auth_face_detector_model_path: str = "~/.local/share/argos/face-models/face_detection_yunet_2023mar.onnx"
    auth_face_recognizer_model_path: str = "~/.local/share/argos/face-models/face_recognition_sface_2021dec.onnx"
    auth_face_sface_threshold: float = 0.363
    auth_alert_command: str = ""
    auth_warning_sound_enabled: bool = True
    auth_warning_delay_seconds: float = 10.0
    auth_alert_delay_seconds: float = 30.0
    auth_warning_interval_seconds: float = 10.0
    kokoro_voice: str = "jf_alpha"
    kokoro_speed: float = 1.0
    kokoro_repo_id: str = "hexgrad/Kokoro-82M"
    kokoro_sample_rate: int = 24000
    whisper_model_size: str = "small"
    whisper_device: str = "auto"
    whisper_compute_type: str = "int8"
    audio_input_devices: tuple[str, ...] = ()
    dashboard_screensaver_seconds: float = 300.0
    audio_state_path: str = "~/.local/state/argos/audio-state.json"


def _load_agent_slots(default_provider: str) -> tuple[AgentSlot, ...]:
    """環境変数からLLMエージェント会話スロットを読み込む。"""
    slots: list[AgentSlot] = []
    default_cwd = os.environ.get("ARGOS_AGENT_CWD", os.environ.get("ARGOS_CODEX_CWD", "/home/pi"))
    index = 1
    while True:
        raw = os.environ.get(f"ARGOS_AGENT_SLOT_{index}", "")
        if not raw:
            break
        parts = [part.strip() for part in raw.split(",", 2)]
        if parts and parts[0]:
            slots.append(
                AgentSlot(
                    name=parts[0],
                    provider=parts[1] if len(parts) > 1 and parts[1] else default_provider,
                    cwd=parts[2] if len(parts) > 2 and parts[2] else default_cwd,
                )
            )
        index += 1
    if slots:
        return tuple(slots)
    legacy_slots = _load_legacy_codex_slots(default_provider, default_cwd)
    if legacy_slots:
        return legacy_slots
    return (
        AgentSlot(
            name=os.environ.get("ARGOS_AGENT_SLOT_NAME", os.environ.get("ARGOS_CODEX_SLOT_NAME", "デフォルト")),
            provider=default_provider,
            cwd=default_cwd,
        ),
    )


def _load_legacy_codex_slots(default_provider: str, default_cwd: str) -> tuple[AgentSlot, ...]:
    """旧ARGOS_CODEX_SLOT形式を互換のため読み込む。"""
    slots: list[AgentSlot] = []
    index = 1
    while True:
        raw = os.environ.get(f"ARGOS_CODEX_SLOT_{index}", "")
        if not raw:
            break
        parts = [part.strip() for part in raw.split(",", 3)]
        if parts and parts[0]:
            slots.append(
                AgentSlot(
                    name=parts[0],
                    provider=default_provider,
                    cwd=parts[1] if len(parts) > 1 and parts[1] else default_cwd,
                )
            )
        index += 1
    return tuple(slots)


def _split_device_env(name: str) -> tuple[str, ...]:
    """環境変数を録音デバイス候補として読み込む。"""
    raw = os.environ.get(name, "")
    if not raw.strip():
        return ()
    if ";" in raw:
        return tuple(part.strip() for part in raw.split(";") if part.strip())
    import re

    devices = re.findall(r"(?:plug)?hw:CARD=[^,\s;]+,DEV=\d+|(?:sysdefault|front|dsnoop):CARD=[^,\s;]+(?:,DEV=\d+)?", raw)
    if devices:
        return tuple(devices)
    return (raw.strip(),)


def _load_audio_input_devices() -> tuple[str, ...]:
    """互換の環境変数名から録音デバイス候補を読み込む。"""
    for name in ("AUDIO_INPUT_DEVICES", "ARGOS_AUDIO_INPUT_DEVICES", "ARGOS_INPUT_DEVICES", "ARGOS_INPUT_DEVICE"):
        devices = _split_device_env(name)
        if devices:
            return devices
    return ()


def load_settings() -> Settings:
    """環境変数と .env から設定を構築する。"""
    extra_args = tuple(arg for arg in os.environ.get("ARGOS_CODEX_EXTRA_ARGS", "").split() if arg)
    antigravity_extra_args = tuple(arg for arg in os.environ.get("ARGOS_ANTIGRAVITY_EXTRA_ARGS", "").split() if arg)
    hermes_extra_args = tuple(arg for arg in os.environ.get("ARGOS_HERMES_EXTRA_ARGS", "").split() if arg)
    agent_provider = os.environ.get("ARGOS_AGENT_PROVIDER", "codex")
    return Settings(
        agent_provider=agent_provider,
        agent_state_path=os.environ.get("ARGOS_AGENT_STATE_PATH", "~/.argos/agent-sessions.json"),
        agent_slots=_load_agent_slots(agent_provider),
        stt_gateway_url=os.environ.get("STT_GATEWAY_URL", ""),
        stt_language=os.environ.get("STT_GATEWAY_LANGUAGE", "ja"),
        stt_gateway_token=os.environ.get("STT_GATEWAY_BEARER_TOKEN", ""),
        tts_filter_url=os.environ.get("TTS_FILTER_URL", ""),
        tts_filter_token=os.environ.get("TTS_FILTER_BEARER_TOKEN", ""),
        tts_delimiters=os.environ.get("ARGOS_TTS_DELIMITERS", "。！？!?"),
        voicevox_url=os.environ.get("VOICEVOX_URL", ""),
        voicevox_speaker=int(os.environ.get("VOICEVOX_SPEAKER", "2")),
        voicevox_sample_rate=int(os.environ.get("VOICEVOX_SAMPLE_RATE", "48000")),
        voicevox_speed_scale=float(os.environ.get("VOICEVOX_SPEED_SCALE", "1.0")),
        audio_input_device=os.environ.get("AUDIO_DEVICE", "plughw:CARD=Microphone,DEV=0"),
        audio_input_devices=_load_audio_input_devices(),
        audio_output_device=os.environ.get("AUDIO_OUTPUT_DEVICE", "default"),
        audio_output_card=os.environ.get("AUDIO_OUTPUT_CARD", ""),
        audio_output_volume=int(os.environ.get("AUDIO_OUTPUT_VOLUME", "90")),
        audio_state_path=os.environ.get("ARGOS_AUDIO_STATE_PATH", "~/.local/state/argos/audio-state.json"),
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
        dashboard_screensaver_seconds=float(os.environ.get("ARGOS_DASHBOARD_SCREENSAVER_SECONDS", "300")),
        ptt_gpio=int(os.environ.get("ARGOS_PTT_GPIO", os.environ.get("PI3_PTT_GPIO", "17"))),
        silence_rms_threshold=float(os.environ.get("SILENCE_RMS_THRESHOLD", "200")),
        dry_run=_bool_env("DRY_RUN", False),
        codex_home=os.environ.get("ARGOS_CODEX_HOME", ""),
        codex_model=os.environ.get("ARGOS_CODEX_MODEL", ""),
        codex_sandbox=os.environ.get("ARGOS_CODEX_SANDBOX", "workspace-write"),
        codex_bypass_sandbox=_bool_env("ARGOS_CODEX_BYPASS_SANDBOX", False),
        codex_approval_policy=os.environ.get("ARGOS_CODEX_APPROVAL", "on-request"),
        codex_extra_args=extra_args,
        antigravity_command=os.environ.get("ARGOS_ANTIGRAVITY_COMMAND", "/home/yuki/.local/bin/agy"),
        antigravity_home=os.environ.get("ARGOS_ANTIGRAVITY_HOME", "~/.gemini/antigravity-cli"),
        antigravity_extra_args=antigravity_extra_args,
        antigravity_skip_permissions=_bool_env("ARGOS_ANTIGRAVITY_SKIP_PERMISSIONS", False),
        antigravity_sandbox=_bool_env("ARGOS_ANTIGRAVITY_SANDBOX", False),
        antigravity_print_timeout=os.environ.get("ARGOS_ANTIGRAVITY_PRINT_TIMEOUT", "5m0s"),
        antigravity_continue_session=_bool_env("ARGOS_ANTIGRAVITY_CONTINUE_SESSION", False),
        antigravity_resume_saved=_bool_env("ARGOS_ANTIGRAVITY_RESUME_SAVED", False),
        antigravity_prompt_prefix=os.environ.get(
            "ARGOS_ANTIGRAVITY_PROMPT_PREFIX",
            "",
        ),
        hermes_command=os.environ.get("ARGOS_HERMES_COMMAND", "hermes"),
        hermes_model=os.environ.get("ARGOS_HERMES_MODEL", ""),
        hermes_provider=os.environ.get("ARGOS_HERMES_PROVIDER", ""),
        hermes_toolsets=os.environ.get("ARGOS_HERMES_TOOLSETS", ""),
        hermes_skills=os.environ.get("ARGOS_HERMES_SKILLS", ""),
        hermes_source=os.environ.get("ARGOS_HERMES_SOURCE", "argos"),
        hermes_pass_session_id=_bool_env("ARGOS_HERMES_PASS_SESSION_ID", True),
        hermes_resume_saved=_bool_env("ARGOS_HERMES_RESUME_SAVED", True),
        hermes_extra_args=hermes_extra_args,
        codex_progress_voice=_bool_env("ARGOS_CODEX_PROGRESS_VOICE", True),
        codex_progress_first_delay_seconds=float(
            os.environ.get("ARGOS_CODEX_PROGRESS_FIRST_DELAY_SECONDS", "8")
        ),
        codex_progress_interval_seconds=float(
            os.environ.get("ARGOS_CODEX_PROGRESS_INTERVAL_SECONDS", "20")
        ),
        greeting_enabled=_bool_env("ARGOS_GREETING_ENABLED", True),
        greeting_state_path=os.environ.get(
            "ARGOS_GREETING_STATE_PATH",
            "~/.local/state/argos/greeting-state.json",
        ),
        startup_splash_enabled=_bool_env("ARGOS_STARTUP_SPLASH_ENABLED", True),
        startup_splash_seconds=float(os.environ.get("ARGOS_STARTUP_SPLASH_SECONDS", "3")),
        startup_sound_enabled=_bool_env("ARGOS_STARTUP_SOUND_ENABLED", True),
        auth_enabled=_bool_env("ARGOS_AUTH_ENABLED", False),
        auth_keyword_hash=os.environ.get("ARGOS_AUTH_KEYWORD_HASH", ""),
        auth_trust_seconds=int(os.environ.get("ARGOS_AUTH_TRUST_SECONDS", "1800")),
        auth_failure_threshold=int(os.environ.get("ARGOS_AUTH_FAILURE_THRESHOLD", "3")),
        auth_face_enabled=_bool_env("ARGOS_AUTH_FACE_ENABLED", False),
        auth_face_samples_dir=os.environ.get("ARGOS_AUTH_FACE_SAMPLES_DIR", "~/.local/share/argos/face-auth"),
        auth_face_capture_command=os.environ.get(
            "ARGOS_AUTH_FACE_CAPTURE_COMMAND",
            "rpicam-still --nopreview --timeout 700 --width 640 --height 480 -o {path}",
        ),
        auth_face_capture_path=os.environ.get("ARGOS_AUTH_FACE_CAPTURE_PATH", "/tmp/argos/auth-face.jpg"),
        auth_face_image_rotation=int(os.environ.get("ARGOS_AUTH_FACE_IMAGE_ROTATION", "0")),
        auth_face_threshold=int(os.environ.get("ARGOS_AUTH_FACE_THRESHOLD", "68")),
        auth_face_min_matches=int(os.environ.get("ARGOS_AUTH_FACE_MIN_MATCHES", "1")),
        auth_face_detection_enabled=_bool_env("ARGOS_AUTH_FACE_DETECTION_ENABLED", True),
        auth_face_min_detected_faces=int(os.environ.get("ARGOS_AUTH_FACE_MIN_DETECTED_FACES", "1")),
        auth_face_max_detected_faces=int(os.environ.get("ARGOS_AUTH_FACE_MAX_DETECTED_FACES", "1")),
        auth_face_detector_model_path=os.environ.get(
            "ARGOS_AUTH_FACE_DETECTOR_MODEL_PATH",
            "~/.local/share/argos/face-models/face_detection_yunet_2023mar.onnx",
        ),
        auth_face_recognizer_model_path=os.environ.get(
            "ARGOS_AUTH_FACE_RECOGNIZER_MODEL_PATH",
            "~/.local/share/argos/face-models/face_recognition_sface_2021dec.onnx",
        ),
        auth_face_sface_threshold=float(os.environ.get("ARGOS_AUTH_FACE_SFACE_THRESHOLD", "0.363")),
        auth_alert_command=os.environ.get("ARGOS_AUTH_ALERT_COMMAND", ""),
        auth_warning_sound_enabled=_bool_env("ARGOS_AUTH_WARNING_SOUND_ENABLED", True),
        auth_warning_delay_seconds=float(os.environ.get("ARGOS_AUTH_WARNING_DELAY_SECONDS", "10")),
        auth_alert_delay_seconds=float(os.environ.get("ARGOS_AUTH_ALERT_DELAY_SECONDS", "30")),
        auth_warning_interval_seconds=float(os.environ.get("ARGOS_AUTH_WARNING_INTERVAL_SECONDS", "10")),
        kokoro_voice=os.environ.get("ARGOS_KOKORO_VOICE", "jf_alpha"),
        kokoro_speed=float(os.environ.get("ARGOS_KOKORO_SPEED", "1.0")),
        kokoro_repo_id=os.environ.get("ARGOS_KOKORO_REPO_ID", "hexgrad/Kokoro-82M"),
        kokoro_sample_rate=int(os.environ.get("ARGOS_KOKORO_SAMPLE_RATE", "24000")),
        whisper_model_size=os.environ.get("ARGOS_WHISPER_MODEL_SIZE", "small"),
        whisper_device=os.environ.get("ARGOS_WHISPER_DEVICE", "auto"),
        whisper_compute_type=os.environ.get("ARGOS_WHISPER_COMPUTE_TYPE", "int8"),
    )


def ensure_runtime_dirs() -> None:
    """ARGOS が使う実行時ディレクトリを作成する。"""
    Path("/tmp/argos").mkdir(parents=True, exist_ok=True)
