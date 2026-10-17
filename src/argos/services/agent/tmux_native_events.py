"""CLI固有の会話IDと完了本文を、所有世代付きイベントへ正規化する。"""

from __future__ import annotations

import fcntl
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from argos.services.agent.claude_tmux_events import MAX_EVENT_BYTES, record_event
from argos.services.agent.hook_text import optional_hook_text
from argos.services.agent.claude_tmux_session import write_private_json


MAX_TRANSCRIPT_BYTES = 64 * MAX_EVENT_BYTES
TRANSCRIPT_RETRY_COUNT = 20
TRANSCRIPT_RETRY_INTERVAL_SECONDS = 0.1
MAX_NATIVE_ID_LENGTH = 200
NO_STEP_INDEX = -1


@dataclass(frozen=True)
class NativeEvent:
    """信頼する項目を限定したCLIの完了通知。"""

    event: str
    native_id: str
    turn_id: str
    text: str
    transcript: str
    error: str
    is_idle: bool


class DuplicateStopError(RuntimeError):
    """ログが進んでいない同一完了の再通知。"""


@dataclass(frozen=True)
class TranscriptProgress:
    """確定回答と、ユーザー入力を含むログ全体の進行位置。"""

    text: str
    answer_step: int
    latest_step: int


def parse_native_event(payload: object, provider: str, event: str) -> NativeEvent:
    """両CLIのキー差を境界で吸収する。"""
    if not isinstance(payload, dict):
        raise ValueError("フック入力はJSONオブジェクトにしてください")
    is_agy = provider == "antigravity"
    native_id = payload.get("conversationId" if is_agy else "session_id")
    if not isinstance(native_id, str) or not native_id or len(native_id) > MAX_NATIVE_ID_LENGTH:
        raise ValueError("フックの会話IDまたは本文の型が不正です")
    return NativeEvent(event, native_id, _text_field(payload, "turn_id"), _text_field(payload, "last_assistant_message"),
                       _text_field(payload, "transcriptPath"), _text_field(payload, "error"), payload.get("fullyIdle", True) is True)


def _text_field(payload: dict[str, object], name: str) -> str:
    """文字列以外の本文やパスを下位層へ渡さない。"""
    return optional_hook_text(payload.get(name, ""), name)


def agy_answer(event: NativeEvent, last_step: int) -> tuple[str, int]:
    """Stopに遅れて反映されるログを短く待ち、前ターンの回答は返さない。"""
    path = Path(event.transcript).expanduser()
    if path.name != "transcript_full.jsonl" or path.parent.parent.parent.name != event.native_id:
        raise ValueError("agyの会話IDとtranscriptの場所が一致しません")
    for _attempt in range(TRANSCRIPT_RETRY_COUNT):
        progress = _read_agy_answer(path, last_step)
        if progress.text:
            return progress.text, progress.answer_step
        time.sleep(TRANSCRIPT_RETRY_INTERVAL_SECONDS)
    if last_step >= 0 and progress.latest_step <= last_step:
        raise DuplicateStopError("処理済みのagy完了通知です")
    raise RuntimeError("agyの確定回答が会話ログに反映されていません")


def _read_agy_answer(path: Path, last_step: int) -> TranscriptProgress:
    """上限付きでログを読み、確定したMODEL応答の最新stepだけを選ぶ。"""
    answer = ""
    latest_step = NO_STEP_INDEX
    if not path.exists():
        return TranscriptProgress(answer, last_step, latest_step)
    if path.stat().st_size > MAX_TRANSCRIPT_BYTES:
        raise ValueError("agyの会話ログが読込上限を超えています")
    with path.open("rb") as source:
        while line := source.readline(MAX_EVENT_BYTES + 1):
            if len(line) > MAX_EVENT_BYTES:
                raise ValueError("agyのログの1行が読込上限を超えています")
            if not line.endswith(b"\n"):
                break
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("agyのログの項目が不正です")
            if type(value.get("step_index")) is int:
                latest_step = max(latest_step, value["step_index"])
            text, step = _done_answer(value, last_step)
            if text:
                answer, last_step = text, step
    return TranscriptProgress(answer, last_step, latest_step)


def _done_answer(value: dict[str, object], last_step: int) -> tuple[str, int]:
    """過去の回答・ツール結果・未確定本文を読み上げない。"""
    step, text = value.get("step_index"), value.get("content")
    is_done = value.get("source") == "MODEL" and value.get("type") == "PLANNER_RESPONSE" and value.get("status") == "DONE"
    if is_done and type(step) is int and step > last_step and isinstance(text, str) and text.strip():
        return text, step
    return "", last_step


def record_native(directory: Path, generation: str, provider: str, event: NativeEvent) -> None:
    """古いCLIや別会話のフックを同じスロットへ混ぜない。"""
    descriptor = os.open(directory / "lifecycle.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "a") as lifecycle:
        fcntl.flock(lifecycle, fcntl.LOCK_SH)
        state = json.loads((directory / "session.json").read_text())
        if state["session_id"] != generation:
            return
        descriptor = os.open(directory / "native.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            _record_bound_event(directory, generation, provider, event)


def _record_bound_event(directory: Path, generation: str, provider: str, event: NativeEvent) -> None:
    """nativeの会話ID・ターンID・stepの既読位置を永続化する。"""
    path = directory / "native.json"
    saved = json.loads(path.read_text()) if path.exists() else {}
    if saved.get("generation") != generation:
        saved = {"generation": generation, "native_id": event.native_id, "last_step": NO_STEP_INDEX, "turn_id": "", "is_ready": False}
    if saved["native_id"] != event.native_id:
        raise ValueError("常駐CLIの会話IDが変わりました。スロットをリセットしてください")
    if event.event == "SessionStart":
        write_private_json(path, json.dumps(saved))
        return
    try:
        kind, text, saved["last_step"] = _completion(event, provider, saved["last_step"])
    except DuplicateStopError:
        return
    except (OSError, ValueError, RuntimeError):
        kind, text = "StopFailure", ""
    if kind == "Stop" and event.turn_id and saved.get("completed_turn_id") == event.turn_id:
        return
    if not saved["is_ready"] and kind == "Stop":
        _append_normalized(directory, generation, "SessionStart", "")
    _append_normalized(directory, generation, kind, text)
    saved.update({"turn_id": event.turn_id, "is_ready": True})
    if kind == "Stop" and event.turn_id:
        saved["completed_turn_id"] = event.turn_id
    write_private_json(path, json.dumps(saved))


def _completion(event: NativeEvent, provider: str, last_step: int) -> tuple[str, str, int]:
    """処理完了と中断を区別し、空回答を成功にしない。"""
    if event.event == "Interrupt" or event.error or (not event.is_idle and provider != "antigravity"):
        return "StopFailure", "", last_step
    text = event.text
    if provider == "antigravity":
        text, last_step = agy_answer(event, last_step)
    kind = "Stop" if text.strip() else "StopFailure"
    return kind, text, last_step


def _append_normalized(directory: Path, generation: str, event: str, text: str) -> None:
    """台帳の上限とパーミッションは既存Claude経路と共有する。"""
    record_event(directory / "events.jsonl", json.dumps({"hook_event_name": event, "session_id": generation, "last_assistant_message": text}))
