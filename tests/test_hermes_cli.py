import json
from pathlib import Path

from argos.config import AgentSlot, Settings
from argos.services.hermes.cli import HermesCliClient, _extract_session_id, _strip_session_info


def _settings(tmp_path: Path) -> Settings:
    """テスト用の最小設定を返す。"""
    cwd = tmp_path / "work"
    cwd.mkdir()
    return Settings(
        agent_provider="hermes",
        agent_state_path=str(tmp_path / "argos-state" / "agent-sessions.json"),
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
        agent_slots=(AgentSlot("Hermes", "hermes", str(cwd)),),
        codex_home="",
        codex_model="",
        codex_sandbox="workspace-write",
        codex_bypass_sandbox=False,
        codex_approval_policy="on-request",
        codex_extra_args=(),
        antigravity_command="/tmp/agy",
        antigravity_home="~/.gemini/antigravity-cli",
        antigravity_extra_args=(),
        hermes_command="/tmp/hermes",
        hermes_model="model-a",
        hermes_provider="provider-a",
        hermes_toolsets="tools-a",
        hermes_skills="skills-a",
        hermes_source="argos-test",
        hermes_pass_session_id=True,
        hermes_resume_saved=True,
        hermes_extra_args=("--yolo",),
    )


def test_hermes_ask_stream_saves_session_id(monkeypatch, tmp_path):
    """Hermes応答を返し、出力内のsession IDを保存する。"""
    settings = _settings(tmp_path)
    calls = []

    class FakeProc:
        returncode = 0

        def communicate(self):
            return "こんにちは\nSession ID: sess-1\n", ""

    def fake_popen(command, stdout, stderr, text, cwd, env):
        calls.append((command, cwd))
        return FakeProc()

    monkeypatch.setattr("argos.services.hermes.cli.subprocess.Popen", fake_popen)
    client = HermesCliClient(settings)

    assert client.ask("依頼") == "こんにちは"
    command, cwd = calls[0]
    assert command == [
        "/tmp/hermes",
        "chat",
        "-q",
        "依頼",
        "-Q",
        "--source",
        "argos-test",
        "--model",
        "model-a",
        "--provider",
        "provider-a",
        "--toolsets",
        "tools-a",
        "--skills",
        "skills-a",
        "--pass-session-id",
        "--yolo",
    ]
    assert cwd == settings.agent_slots[0].cwd
    assert "sess-1" in Path(settings.agent_state_path).read_text(encoding="utf-8")


def test_hermes_uses_saved_session(monkeypatch, tmp_path):
    """保存済みsession IDがあれば--resumeで継続する。"""
    settings = _settings(tmp_path)
    client = HermesCliClient(settings)
    Path(settings.agent_state_path).parent.mkdir(parents=True, exist_ok=True)
    from argos.services.hermes.cli import _slot_key

    Path(settings.agent_state_path).write_text(
        json.dumps({_slot_key(settings.agent_slots[0]): "saved-session"}),
        encoding="utf-8",
    )
    calls = []

    class FakeProc:
        returncode = 0

        def communicate(self):
            return "続きの応答\n", ""

    def fake_popen(command, stdout, stderr, text, cwd, env):
        calls.append(command)
        return FakeProc()

    monkeypatch.setattr("argos.services.hermes.cli.subprocess.Popen", fake_popen)

    client = HermesCliClient(settings)
    assert client.ask("続き") == "続きの応答"
    assert "--resume" in calls[0]
    assert "saved-session" in calls[0]


def test_extract_and_strip_session_info():
    """session ID行の抽出と除去ができる。"""
    output = "応答\nsession_id: abc-123\n"

    assert _extract_session_id(output) == "abc-123"
    assert _strip_session_info(output) == "応答"

