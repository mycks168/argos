import json
from pathlib import Path

from argos.config import CodexSlot, Settings
from argos.services.codex.cli import CodexCliClient


def _settings(tmp_path):
    return Settings(
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
        codex_slots=(CodexSlot("作業", str(tmp_path), str(tmp_path / "codex-home"), "gpt-5"),),
        codex_sandbox="workspace-write",
        codex_bypass_sandbox=False,
        codex_approval_policy="on-request",
        codex_extra_args=("--json",),
    )


def test_ask_starts_and_resumes(monkeypatch, tmp_path):
    calls = []

    class FakeStdin:
        def __init__(self):
            self.value = ""

        def write(self, text):
            self.value += text

        def close(self):
            pass

    class FakeStderr:
        def read(self):
            return ""

    class FakeProc:
        def __init__(self, command, cwd, env):
            self.command = command
            self.stdin = FakeStdin()
            self.stderr = FakeStderr()
            self.stdout = iter(
                [
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {"id": "019e71e4-27fb-74d1-82a2-9b0ab58f0846"},
                        }
                    )
                    + "\n",
                    json.dumps(
                        {
                            "type": "event_msg",
                            "payload": {"type": "agent_message", "phase": "final_answer", "message": "応答です"},
                        }
                    )
                    + "\n"
                ]
            )
            self.cwd = cwd
            self.env = env

        def wait(self, timeout=None):
            output_file = self.command[self.command.index("-o") + 1]
            Path(output_file).write_text("応答です", encoding="utf-8")
            calls.append((self.command, self.stdin.value, self.cwd, self.env))
            return 0

    def fake_popen(command, stdin, stdout, stderr, text, cwd, env):
        return FakeProc(command, cwd, env)

    monkeypatch.setattr("argos.services.codex.cli.subprocess.Popen", fake_popen)
    client = CodexCliClient(_settings(tmp_path))

    assert client.ask("こんにちは") == "応答です"
    assert client.ask("続き") == "応答です"

    assert calls[0][0][:2] == ["codex", "exec"]
    assert "resume" not in calls[0][0]
    assert calls[1][0][:3] == ["codex", "exec", "resume"]
    assert "--last" not in calls[1][0]
    assert "019e71e4-27fb-74d1-82a2-9b0ab58f0846" in calls[1][0]
    assert "-a" not in calls[0][0]
    assert "-C" in calls[0][0]
    assert "-s" in calls[0][0]
    assert "-C" not in calls[1][0]
    assert "-s" not in calls[1][0]
    assert calls[0][1] == "こんにちは"
    assert calls[0][2] == str(tmp_path)
    assert calls[0][3]["CODEX_HOME"] == str(tmp_path / "codex-home")


def test_ask_resumes_persisted_session_after_restart(monkeypatch, tmp_path):
    calls = []

    class FakeStdin:
        def __init__(self):
            self.value = ""

        def write(self, text):
            self.value += text

        def close(self):
            pass

    class FakeStderr:
        def read(self):
            return ""

    class FakeProc:
        def __init__(self, command, cwd, env):
            self.command = command
            self.stdin = FakeStdin()
            self.stderr = FakeStderr()
            self.stdout = iter(
                [
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {"id": "019e71e4-27fb-74d1-82a2-9b0ab58f0846"},
                        }
                    )
                    + "\n",
                    json.dumps(
                        {
                            "type": "event_msg",
                            "payload": {"type": "agent_message", "phase": "final_answer", "message": "応答です"},
                        }
                    )
                    + "\n",
                ]
            )
            self.cwd = cwd
            self.env = env

        def wait(self, timeout=None):
            output_file = self.command[self.command.index("-o") + 1]
            Path(output_file).write_text("応答です", encoding="utf-8")
            calls.append((self.command, self.stdin.value, self.cwd, self.env))
            return 0

    def fake_popen(command, stdin, stdout, stderr, text, cwd, env):
        return FakeProc(command, cwd, env)

    monkeypatch.setattr("argos.services.codex.cli.subprocess.Popen", fake_popen)
    settings = _settings(tmp_path)

    assert CodexCliClient(settings).ask("初回") == "応答です"
    assert CodexCliClient(settings).ask("再起動後") == "応答です"

    assert "resume" not in calls[0][0]
    assert calls[1][0][:3] == ["codex", "exec", "resume"]
    assert "019e71e4-27fb-74d1-82a2-9b0ab58f0846" in calls[1][0]
    assert calls[1][1] == "再起動後"


def test_ask_saves_session_id_from_session_file(monkeypatch, tmp_path):
    calls = []
    session_id = "019e7227-f7ad-74a3-98ee-fb8c5ac4c165"

    class FakeStdin:
        def __init__(self):
            self.value = ""

        def write(self, text):
            self.value += text

        def close(self):
            pass

    class FakeStderr:
        def read(self):
            return ""

    class FakeProc:
        def __init__(self, command, cwd, env):
            self.command = command
            self.stdin = FakeStdin()
            self.stderr = FakeStderr()
            self.stdout = iter(
                [
                    json.dumps(
                        {
                            "type": "event_msg",
                            "payload": {"type": "agent_message", "phase": "final_answer", "message": "応答です"},
                        }
                    )
                    + "\n",
                ]
            )
            self.cwd = cwd
            self.env = env

        def wait(self, timeout=None):
            output_file = self.command[self.command.index("-o") + 1]
            Path(output_file).write_text("応答です", encoding="utf-8")
            session_file = (
                Path(self.env["CODEX_HOME"])
                / "sessions"
                / "2026"
                / "05"
                / "29"
                / f"rollout-2026-05-29T14-14-42-{session_id}.jsonl"
            )
            session_file.parent.mkdir(parents=True, exist_ok=True)
            session_file.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"id": session_id, "cwd": self.cwd, "originator": "codex_exec"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            calls.append((self.command, self.stdin.value, self.cwd, self.env))
            return 0

    def fake_popen(command, stdin, stdout, stderr, text, cwd, env):
        return FakeProc(command, cwd, env)

    monkeypatch.setattr("argos.services.codex.cli.subprocess.Popen", fake_popen)
    settings = _settings(tmp_path)

    assert CodexCliClient(settings).ask("初回") == "応答です"
    assert CodexCliClient(settings).ask("再起動後") == "応答です"

    assert "resume" not in calls[0][0]
    assert calls[1][0][:3] == ["codex", "exec", "resume"]
    assert session_id in calls[1][0]


def test_ask_raises_on_codex_error(monkeypatch, tmp_path):
    class FakeStdin:
        def write(self, text):
            pass

        def close(self):
            pass

    class FakeStderr:
        def read(self):
            return "失敗"

    class FakeProc:
        stdin = FakeStdin()
        stdout = iter([])
        stderr = FakeStderr()

        def wait(self, timeout=None):
            return 2

    monkeypatch.setattr("argos.services.codex.cli.subprocess.Popen", lambda *args, **kwargs: FakeProc())
    client = CodexCliClient(_settings(tmp_path))

    try:
        client.ask("壊れる")
    except RuntimeError as exc:
        assert "codex-cli エラー" in str(exc)
    else:
        raise AssertionError("RuntimeError が発生しませんでした")


def test_slot_switch_and_reset(tmp_path):
    settings = _settings(tmp_path)
    settings = Settings(
        **{
            **settings.__dict__,
            "codex_slots": (
                CodexSlot("一番", str(tmp_path), str(tmp_path / "home-a"), ""),
                CodexSlot("二番", str(tmp_path), str(tmp_path / "home-b"), ""),
            ),
        }
    )
    client = CodexCliClient(settings)

    assert client.current_name == "一番"
    assert client.next_slot() == "二番"
    client.reset_current()


def test_bypass_sandbox_adds_codex_bypass_flag(tmp_path):
    settings = Settings(**{**_settings(tmp_path).__dict__, "codex_bypass_sandbox": True})
    client = CodexCliClient(settings)
    conversation = client._conversations[0]

    first_command = client._build_command(conversation, "/tmp/out.txt")
    conversation.session_id = "019e7232-e37d-7ab1-a2f4-9d0d70dfe633"
    resume_command = client._build_command(conversation, "/tmp/out.txt")

    assert "--dangerously-bypass-approvals-and-sandbox" in first_command
    assert "--dangerously-bypass-approvals-and-sandbox" in resume_command
    assert "-s" not in first_command
