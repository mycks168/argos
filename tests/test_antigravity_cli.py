import json
from pathlib import Path

from argos.config import AgentSlot, Settings
from argos.services.antigravity.cli import (
    AntigravityCliClient,
    _count_lines,
    _extract_latest_done_planner_response,
    _slot_key,
)


def _settings(tmp_path: Path) -> Settings:
    """テスト用の最小設定を返す。"""
    cwd = tmp_path / "work"
    cwd.mkdir()
    return Settings(
        agent_provider="antigravity",
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
        agent_slots=(AgentSlot("AG", "antigravity", str(cwd)),),
        codex_home="",
        codex_model="",
        codex_sandbox="workspace-write",
        codex_bypass_sandbox=False,
        codex_approval_policy="on-request",
        codex_extra_args=(),
        antigravity_command="/tmp/agy",
        antigravity_home=str(tmp_path / "antigravity-home"),
        antigravity_extra_args=("--x",),
        antigravity_skip_permissions=True,
        antigravity_sandbox=True,
        antigravity_print_timeout="30s",
        antigravity_continue_session=False,
        antigravity_resume_saved=False,
        antigravity_prompt_prefix="",
    )


class FakeStdout:
    """1文字ずつ読める標準出力。"""

    def __init__(self, text: str) -> None:
        """出力文字列を初期化する。"""
        self._text = text
        self._index = 0

    def read(self, size: int = 1) -> str:
        """指定サイズ分の文字を返す。"""
        if self._index >= len(self._text):
            return ""
        value = self._text[self._index : self._index + size]
        self._index += size
        return value


class FakeStderr:
    """空の標準エラー。"""

    def read(self) -> str:
        """標準エラー文字列を返す。"""
        return ""


def _write_transcript(settings: Settings, conversation_id: str, entries: list[dict]) -> None:
    """Antigravity transcript のテストデータを書き込む。"""
    path = (
        Path(settings.antigravity_home)
        / "brain"
        / conversation_id
        / ".system_generated"
        / "logs"
        / "transcript_full.jsonl"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries),
        encoding="utf-8",
    )


def test_antigravity_ask_stream_saves_conversation(monkeypatch, tmp_path):
    """transcriptから回答を読み、最新会話IDをArgos状態へ保存する。"""
    settings = _settings(tmp_path)
    settings = Settings(**{**settings.__dict__, "antigravity_continue_session": True})
    calls = []

    class FakeProc:
        def __init__(self, command, cwd):
            self.command = command
            self.cwd = cwd
            self.returncode = 0

        def communicate(self):
            cache_path = Path(settings.antigravity_home) / "cache" / "last_conversations.json"
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({settings.agent_slots[0].cwd: "conv-1"}), encoding="utf-8")
            _write_transcript(
                settings,
                "conv-1",
                [
                    {"source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "content": "過去の応答"},
                    {"source": "MODEL", "type": "VIEW_FILE", "status": "DONE", "content": "表示"},
                    {"source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "content": "応答"},
                ],
            )
            calls.append((self.command, self.cwd))
            return "過去の応答\n応答\n", ""

    def fake_popen(command, stdout, stderr, text, cwd, env, bufsize):
        return FakeProc(command, cwd)

    monkeypatch.setattr("argos.services.antigravity.cli.subprocess.Popen", fake_popen)

    client = AntigravityCliClient(settings)

    assert "".join(client.ask_stream("こんにちは")) == "応答"
    assert calls[0][0] == [
        "/tmp/agy",
        "--dangerously-skip-permissions",
        "--sandbox",
        "--print-timeout",
        "30s",
        "--x",
        "--print",
        "こんにちは",
    ]
    assert calls[0][1] == settings.agent_slots[0].cwd
    assert "conv-1" in Path(settings.agent_state_path).read_text(encoding="utf-8")


def test_antigravity_uses_cached_conversation(monkeypatch, tmp_path):
    """保存済み会話IDの復元が有効なら継続会話として起動する。"""
    settings = _settings(tmp_path)
    settings = Settings(
        **{
            **settings.__dict__,
            "antigravity_continue_session": True,
            "antigravity_resume_saved": True,
        }
    )
    Path(settings.agent_state_path).parent.mkdir(parents=True, exist_ok=True)
    Path(settings.agent_state_path).write_text(
        json.dumps({_slot_key(settings.agent_slots[0]): "conv-cache"}),
        encoding="utf-8",
    )
    _write_transcript(
        settings,
        "conv-cache",
        [{"source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "content": "古い応答"}],
    )
    calls = []

    class FakeProc:
        returncode = 0

        def communicate(self):
            _write_transcript(
                settings,
                "conv-cache",
                [
                    {"source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "content": "古い応答"},
                    {"source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "content": "応答"},
                ],
            )
            return "古い応答\n応答\n", ""

    def fake_popen(command, stdout, stderr, text, cwd, env, bufsize):
        calls.append(command)
        return FakeProc()

    monkeypatch.setattr("argos.services.antigravity.cli.subprocess.Popen", fake_popen)

    client = AntigravityCliClient(settings)
    assert client.ask("続き") == "応答"
    assert "--conversation" in calls[0]
    assert "conv-cache" in calls[0]


def test_antigravity_does_not_continue_by_default(monkeypatch, tmp_path):
    """既定では保存済み会話IDがあっても新規会話として起動する。"""
    settings = _settings(tmp_path)
    settings = Settings(**{**settings.__dict__, "antigravity_resume_saved": True})
    Path(settings.agent_state_path).parent.mkdir(parents=True, exist_ok=True)
    Path(settings.agent_state_path).write_text(
        json.dumps({_slot_key(settings.agent_slots[0]): "conv-cache"}),
        encoding="utf-8",
    )
    calls = []

    class FakeProc:
        returncode = 0

        def communicate(self):
            cache_path = Path(settings.antigravity_home) / "cache" / "last_conversations.json"
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({settings.agent_slots[0].cwd: "conv-new"}), encoding="utf-8")
            _write_transcript(
                settings,
                "conv-new",
                [{"source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "content": "応答"}],
            )
            return "応答\n", ""

    def fake_popen(command, stdout, stderr, text, cwd, env, bufsize):
        calls.append(command)
        return FakeProc()

    monkeypatch.setattr("argos.services.antigravity.cli.subprocess.Popen", fake_popen)

    client = AntigravityCliClient(settings)
    assert client.ask("新しい質問") == "応答"
    assert "--conversation" not in calls[0]
    assert "conv-cache" in Path(settings.agent_state_path).read_text(encoding="utf-8")


def test_extract_latest_done_planner_response_ignores_tool_rows(tmp_path):
    """追加行の末尾から本文ありの完了PLANNER_RESPONSEだけを返す。"""
    path = tmp_path / "transcript_full.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(entry, ensure_ascii=False)
            for entry in [
                {"source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "content": "古い応答"},
                {"source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "tool_calls": []},
                {"source": "MODEL", "type": "VIEW_FILE", "status": "DONE", "content": "表示"},
                {"source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "content": "今回の応答"},
            ]
        ),
        encoding="utf-8",
    )

    assert _extract_latest_done_planner_response(path, 1) == "今回の応答"


def test_transcript_reader_tolerates_invalid_utf8_bytes(tmp_path):
    """transcriptに不正なバイトが混ざっていても回答抽出を継続する。"""
    path = tmp_path / "transcript_full.jsonl"
    path.write_bytes(
        b'{"source":"MODEL","type":"PLANNER_RESPONSE","status":"DONE","content":"old"}\n'
        + b'{"source":"MODEL","type":"PLANNER_RESPONSE","status":"DONE","content":"bad \\x95 row"}\n'
        + '{"source":"MODEL","type":"PLANNER_RESPONSE","status":"DONE","content":"今回の応答"}\n'.encode("utf-8")
    )

    assert _count_lines(path) == 3
    assert _extract_latest_done_planner_response(path, 1) == "今回の応答"
