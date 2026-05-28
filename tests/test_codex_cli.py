import json
from pathlib import Path

from argos.config import CodexSlot, Settings
from argos.services.codex.cli import CodexCliClient


def _settings(tmp_path):
    return Settings(
        stt_gateway_url="http://stt",
        stt_language="ja",
        tts_filter_url="",
        tts_filter_token="",
        tts_delimiters="。！？!?",
        voicevox_url="http://voicevox",
        voicevox_speaker=2,
        voicevox_sample_rate=48000,
        audio_input_device="in",
        audio_output_device="out",
        audio_output_card="",
        audio_output_volume=90,
        audio_sample_rate=16000,
        ptt_gpio=17,
        silence_rms_threshold=200,
        dry_run=True,
        codex_slots=(CodexSlot("作業", str(tmp_path), "/tmp/codex-home", "gpt-5"),),
        codex_sandbox="workspace-write",
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
    assert "-a" not in calls[0][0]
    assert "-C" in calls[0][0]
    assert "-s" in calls[0][0]
    assert "-C" not in calls[1][0]
    assert "-s" not in calls[1][0]
    assert calls[0][1] == "こんにちは"
    assert calls[0][2] == str(tmp_path)
    assert calls[0][3]["CODEX_HOME"] == "/tmp/codex-home"


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
                CodexSlot("一番", str(tmp_path), "", ""),
                CodexSlot("二番", str(tmp_path), "", ""),
            ),
        }
    )
    client = CodexCliClient(settings)

    assert client.current_name == "一番"
    assert client.next_slot() == "二番"
    client.reset_current()
