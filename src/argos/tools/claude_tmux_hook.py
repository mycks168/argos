"""Claudeの試験スロット用フックを、ネットワークを使わず記録する。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from argos.services.agent.claude_tmux_events import MAX_EVENT_BYTES, record_event
from argos.services.agent.subagent_hooks import record_subagent


def main() -> None:
    """stdinのフック入力を引数で指定された所有ファイルへ保存する。"""
    parser = argparse.ArgumentParser(description="Claude tmuxフックのローカル記録")
    parser.add_argument("path", type=Path)
    arguments = parser.parse_args()
    raw = sys.stdin.read(MAX_EVENT_BYTES + 1)
    if len(raw.encode()) > MAX_EVENT_BYTES:
        raise ValueError("Claudeフック入力は1MiB以内にしてください")
    payload = json.loads(raw)
    if isinstance(payload, dict) and payload.get("hook_event_name") == "SubagentStop":
        record_subagent(arguments.path.parent, str(payload.get("session_id", "")), payload)
    else:
        record_event(arguments.path, raw)
    print("{}")


if __name__ == "__main__":
    main()
