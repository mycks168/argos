import json
from pathlib import Path

from argos.config import AgentSlot, Settings
from argos.services.claude.cli import ClaudeCliClient, _slot_key


def _settings(tmp_path: Path) -> Settings:
    """テスト用の最小設定を返す。"""
    cwd = tmp_path / "work"
    cwd.mkdir()
    return Settings(
        agent_provider="claude",
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
        agent_slots=(AgentSlot("Claude", "claude", str(cwd)),),
        codex_home="",
        codex_model="",
        codex_sandbox="workspace-write",
        codex_bypass_sandbox=False,
        codex_approval_policy="on-request",
        codex_extra_args=(),
        antigravity_command="/tmp/agy",
        antigravity_home="~/.gemini/antigravity-cli",
        antigravity_extra_args=(),
    )


def test_claude_ask_stream_generates_and_saves_session_id(monkeypatch, tmp_path):
    """Claudeの初回呼び出し時に新規セッションIDを自動生成して保存することを確認する。"""
    settings = _settings(tmp_path)
    calls = []

    class FakeProc:
        returncode = 0

        def wait(self):
            return 0

        @property
        def stdout(self):
            lines = [
                '{"type":"system","subtype":"init"}',
                '{"type":"stream_event","event":{"type":"message_start"}}',
                '{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}',
                '{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"こんに"}}}',
                '{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"ちは"}}}',
                '{"type":"stream_event","event":{"type":"content_block_stop","index":0}}',
                '{"type":"stream_event","event":{"type":"message_stop"}}',
                '{"type":"result","total_cost_usd":0.001,"usage":{"inputTokens":10,"outputTokens":5}}',
            ]
            return lines

        @property
        def stderr(self):
            return None

    def fake_popen(command, stdin, stdout, stderr, text, cwd):
        calls.append((command, cwd))
        return FakeProc()

    monkeypatch.setattr("argos.services.claude.cli.subprocess.Popen", fake_popen)
    client = ClaudeCliClient(settings)

    # 1. 応答確認
    assert client.ask("こんにちは") == "こんにちは"

    # 2. 実行コマンドと引数確認
    command, cwd = calls[0]
    assert "--session-id" in command
    # 生成されたUUIDを取り出す
    session_id_idx = command.index("--session-id") + 1
    session_id = command[session_id_idx]

    # 3. セッションIDがファイルに保存されているか確認
    state_data = json.loads(Path(settings.agent_state_path).read_text(encoding="utf-8"))
    assert state_data[_slot_key(settings.agent_slots[0])] == session_id


def test_claude_uses_saved_session(monkeypatch, tmp_path):
    """保存済みのセッションIDがある場合に--resumeが使われることを確認する。"""
    settings = _settings(tmp_path)
    Path(settings.agent_state_path).parent.mkdir(parents=True, exist_ok=True)

    saved_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    Path(settings.agent_state_path).write_text(
        json.dumps({_slot_key(settings.agent_slots[0]): saved_uuid}),
        encoding="utf-8",
    )

    calls = []

    class FakeProc:
        returncode = 0

        def wait(self):
            return 0

        @property
        def stdout(self):
            return [
                '{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"続きです"}}}',
            ]

        @property
        def stderr(self):
            return None

    def fake_popen(command, stdin, stdout, stderr, text, cwd):
        calls.append(command)
        return FakeProc()

    monkeypatch.setattr("argos.services.claude.cli.subprocess.Popen", fake_popen)
    client = ClaudeCliClient(settings)

    assert client.ask("続き") == "続きです"
    assert "--resume" in calls[0]
    assert saved_uuid in calls[0]


def test_claude_reset_current(monkeypatch, tmp_path):
    """reset_currentがセッションIDを完全にクリアすることを確認する。"""
    settings = _settings(tmp_path)
    client = ClaudeCliClient(settings)

    # セッションIDを擬似的にロード・保存しておく
    slot_key = _slot_key(settings.agent_slots[0])
    client._store.save(slot_key, "some-uuid")

    # リセットの実行
    client.reset_current()

    # セッションファイルの中身が空になっているか確認
    state_data = json.loads(Path(settings.agent_state_path).read_text(encoding="utf-8"))
    assert slot_key not in state_data
