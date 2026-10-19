"""子エージェントの通知を親スロットへ固定し、通常Stopと分離する。"""

from __future__ import annotations

import json
from pathlib import Path

from argos.services.agent.background_answers import BackgroundAnswers


def record_subagent(directory: Path, generation: str, payload: object) -> None:
    """子の本文を親のターン完了として扱わず、専用の保存先へ渡す。"""
    if not isinstance(payload, dict):
        raise ValueError("子エージェントのフックはJSONオブジェクトにしてください")
    state = json.loads((directory / "session.json").read_text())
    if state["session_id"] != generation:
        return
    native_path = directory / "native.json"
    expected_session = (
        json.loads(native_path.read_text())["native_id"]
        if native_path.exists()
        else generation
    )
    if payload.get("session_id") != expected_session:
        return
    fields = [
        payload.get(key, "") or ""
        for key in ("agent_id", "agent_type", "last_assistant_message")
    ]
    if not all(isinstance(value, str) for value in fields):
        raise ValueError("子エージェントの識別子と本文は文字列にしてください")
    BackgroundAnswers(directory).record(generation, *fields)
