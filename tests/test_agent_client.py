from argos.config import AgentSlot, Settings
from argos.services.agent import create_agent_client
from argos.services.codex.cli import CodexCliClient


def _settings() -> Settings:
    """テスト用の最小設定を返す。"""
    return Settings(
        agent_provider="codex",
        agent_state_path="~/.argos/agent-sessions.json",
        stt_gateway_url="http://stt",
        stt_language="ja",
        stt_gateway_token="",
        tts_filter_url="",
        tts_filter_token="",
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
        silence_rms_threshold=200,
        dry_run=True,
        agent_slots=(AgentSlot("作業", "codex", "/tmp"),),
        codex_home="",
        codex_model="",
        codex_sandbox="workspace-write",
        codex_bypass_sandbox=False,
        codex_approval_policy="on-request",
        codex_extra_args=(),
    )


def test_create_codex_agent_client():
    """codexプロバイダーではCodexクライアントを作成する。"""
    client = create_agent_client(_settings())

    assert isinstance(client, CodexCliClient)


def test_unknown_agent_provider_raises():
    """未対応プロバイダーは起動時に検出できる。"""
    settings = Settings(**{**_settings().__dict__, "agent_provider": "antigravity"})

    try:
        create_agent_client(settings)
    except ValueError as exc:
        assert "未対応" in str(exc)
    else:
        raise AssertionError("ValueError が発生しませんでした")
