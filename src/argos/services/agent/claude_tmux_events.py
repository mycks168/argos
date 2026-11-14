"""Claudeフック記録の型とローカル入出力。"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from argos.services.agent.hook_text import optional_hook_text


MAX_EVENT_BYTES = 1024 * 1024
MAX_JOURNAL_BYTES = 16 * MAX_EVENT_BYTES


@dataclass(frozen=True)
class ClaudeHookEvent:
    """読み上げに必要な情報だけを残したフック記録。"""

    event: str
    session_id: str
    text: str
    end_offset: int = 0


def completed_answer(events: Iterable[ClaudeHookEvent], session_id: str) -> str:
    """先行する失敗通知があっても、同じ世代の確定本文を見つける。"""
    return next((event.text for event in events if event.session_id == session_id and event.event == "Stop" and event.text.strip()), "")


def record_event(path: Path, payload: str) -> None:
    """機微な本文を標準出力へ出さず、1行ずつ排他記録する。"""
    if len(payload.encode("utf-8")) > MAX_EVENT_BYTES:
        raise ValueError("Claudeフック入力が1MiBを超えています")
    received = json.loads(payload)
    if not isinstance(received, dict):
        raise ValueError("Claudeフック入力はJSONオブジェクトにしてください")
    record = json.dumps({
        "event": received.get("hook_event_name", ""),
        "session_id": received.get("session_id", ""),
        "prompt_id": optional_hook_text(received.get("prompt_id", ""), "prompt_id"),
        "text": optional_hook_text(received.get("last_assistant_message", ""), "last_assistant_message"),
    }, ensure_ascii=False) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as output:
        fcntl.flock(output, fcntl.LOCK_EX)
        if os.fstat(output.fileno()).st_size + len(record.encode("utf-8")) > MAX_JOURNAL_BYTES:
            raise ValueError("Claudeフック記録が16MiBを超えています。試験スロットをリセットしてください")
        output.write(record)


def read_events(path: Path, offset: int) -> tuple[list[ClaudeHookEvent], int]:
    """書き込み途中の末尾行を次回まで保留する。"""
    events: list[ClaudeHookEvent] = []
    if not path.exists():
        return events, offset
    with path.open("rb") as source:
        source.seek(offset)
        while line := source.readline(MAX_EVENT_BYTES + 1):
            if len(line) > MAX_EVENT_BYTES:
                raise RuntimeError(f"Claudeフック記録の行が上限を超えています: {path}")
            if not line.endswith(b"\n"):
                break
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"Claudeフック記録がJSONオブジェクトではありません: {path}")
            event_name = payload.get("event", "")
            session_id = payload.get("session_id", "")
            text = optional_hook_text(payload.get("text", ""), "text")
            if not isinstance(event_name, str) or not isinstance(session_id, str):
                raise ValueError(f"Claudeフック記録の項目が文字列ではありません: {path}")
            events.append(ClaudeHookEvent(event_name, session_id, text, end_offset=source.tell()))
            offset = source.tell()
    return events, offset
