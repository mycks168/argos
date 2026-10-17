"""会話ID・世代・stepの混同と、ログ遅延を検証する。"""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from argos.services.agent.claude_tmux_events import read_events
from argos.services.agent.tmux_native_events import NativeEvent, agy_answer, parse_native_event, record_native


@pytest.fixture
def directory(tmp_path: Path) -> Path:
    """本物の会話ログと別の所有世代を用意する。"""
    (tmp_path / "session.json").write_text('{"session_id":"generation"}')
    return tmp_path


def _event(event: str = "SessionStart") -> NativeEvent:
    """同じCLI会話の通知を作る。"""
    return NativeEvent(event, "native", "turn-1", "回答", "", "", True)


def _transcript(directory: Path, content: str) -> Path:
    """機微な音声・既存ログを使わず、合成したJSONLだけを用意する。"""
    path = directory / "native" / ".system_generated" / "logs" / "transcript_full.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _answer(step: int = 1, text: str = "回答") -> str:
    """回答のstepを明示したログ行を返す。"""
    return json.dumps({"source":"MODEL", "type":"PLANNER_RESPONSE", "status":"DONE", "step_index":step, "content":text}) + "\n"


def test_parse_native() -> None:
    """固有キーと型の差を境界で正規化する。"""
    codex = parse_native_event({"session_id":"native", "turn_id":"turn", "last_assistant_message":"回答"}, "codex", "Stop")
    assert codex.native_id == "native" and codex.text == "回答"
    agy = parse_native_event({"conversationId":"native", "fullyIdle":False}, "antigravity", "Stop")
    assert not agy.is_idle
    for payload in [[], {}, {"session_id":"x", "last_assistant_message":3}]:
        with pytest.raises(ValueError):
            parse_native_event(payload, "codex", "Stop")


def test_binding_and_deduplication(directory: Path) -> None:
    """古い世代と別会話、同じターンの完了再配信を区別する。"""
    record_native(directory, "old", "codex", _event())
    assert not (directory / "native.json").exists()
    record_native(directory, "generation", "codex", _event())
    assert not (directory / "events.jsonl").exists()
    record_native(directory, "generation", "codex", _event("Stop"))
    events, offset = read_events(directory / "events.jsonl", 0)
    assert [event.event for event in events] == ["SessionStart", "Stop"]
    assert events[-1].text == "回答"
    record_native(directory, "generation", "codex", _event("Stop"))
    assert read_events(directory / "events.jsonl", offset)[0] == []
    with pytest.raises(ValueError, match="会話ID"):
        record_native(directory, "generation", "codex", replace(_event(), native_id="other"))
    for event in [replace(_event("Stop"), error="failed", turn_id="turn-2"), _event("Interrupt"), replace(_event("Stop"), is_idle=False, turn_id="turn-3")]:
        record_native(directory, "generation", "codex", event)
    assert read_events(directory / "events.jsonl", offset)[0][-1].event == "StopFailure"


def test_agy_step_tracking(directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同じ会話の古い回答を次のターンへ再配信しない。"""
    monkeypatch.setattr("argos.services.agent.tmux_native_events.time.sleep", Mock())
    path = _transcript(directory, _answer())
    start = replace(_event(), turn_id="", transcript=str(path))
    record_native(directory, "generation", "antigravity", start)
    record_native(directory, "generation", "antigravity", replace(start, event="Stop"))
    saved = json.loads((directory / "native.json").read_text())
    assert saved["last_step"] == 1
    path.write_text(_answer() + _answer(3, "次の回答"))
    record_native(directory, "generation", "antigravity", replace(start, event="Stop"))
    events, offset = read_events(directory / "events.jsonl", 0)
    assert [event.text for event in events if event.event == "Stop"] == ["回答", "次の回答"]
    record_native(directory, "generation", "antigravity", replace(start, event="Stop"))
    assert read_events(directory / "events.jsonl", offset)[0] == []


def test_agy_delayed_log(directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """書込途中の末尾は保留し、確定後にだけ本文を返す。"""
    path = _transcript(directory, _answer().rstrip())
    monkeypatch.setattr("argos.services.agent.tmux_native_events.time.sleep", lambda _seconds: path.write_text(_answer()))
    assert agy_answer(replace(_event("Stop"), transcript=str(path)), -1) == ("回答", 1)


def test_agy_parent_reply_does_not_wait_for_children(directory: Path) -> None:
    """子が動作中でも、確定した親の返事は取得して次の入力を許す。"""
    path = _transcript(directory, _answer())
    event = replace(_event("Stop"), transcript=str(path), is_idle=False)
    record_native(directory, "generation", "antigravity", event)
    events, _offset = read_events(directory / "events.jsonl", 0)
    assert events[-1].event == "Stop" and events[-1].text == "回答"


def test_agy_bad_logs(directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """不正パス・異常データ・過大なログを正常な回答としない。"""
    monkeypatch.setattr("argos.services.agent.tmux_native_events.time.sleep", Mock())
    path = _transcript(directory, "[]\n")
    event = replace(_event("Stop"), transcript=str(path))
    with pytest.raises(ValueError):
        agy_answer(replace(event, transcript="/tmp/other.jsonl"), -1)
    with pytest.raises(ValueError):
        agy_answer(event, -1)
    path.unlink()
    with pytest.raises(RuntimeError):
        agy_answer(event, -1)
    path.write_text(_answer(0, "") + '{"type":"USER_INPUT","step_index":2}\n')
    with pytest.raises(RuntimeError):
        agy_answer(event, 0)
    monkeypatch.setattr("argos.services.agent.tmux_native_events.MAX_EVENT_BYTES", 10)
    monkeypatch.setattr("argos.services.agent.tmux_native_events.MAX_TRANSCRIPT_BYTES", 640)
    path.write_text("x" * 50)
    with pytest.raises(ValueError, match="1行"):
        agy_answer(event, -1)
    path.write_text("x" * 641)
    with pytest.raises(ValueError, match="会話ログ"):
        agy_answer(event, -1)


def test_incomplete_stop_is_failure(directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """空のStopや取得不能ログを起動成功にしない。"""
    monkeypatch.setattr("argos.services.agent.tmux_native_events.time.sleep", Mock())
    record_native(directory, "generation", "codex", replace(_event("Stop"), text=""))
    events, offset = read_events(directory / "events.jsonl", 0)
    assert events[-1].event == "StopFailure"
    record_native(directory, "generation", "antigravity", replace(_event("Stop"), transcript="/tmp/invalid", turn_id="other"))
    assert read_events(directory / "events.jsonl", offset)[0][-1].event == "StopFailure"
