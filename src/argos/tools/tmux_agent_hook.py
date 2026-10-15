"""Codex・agyの完了通知をネットワークなしで記録する。"""

import argparse
import json
import sys
from pathlib import Path

from argos.services.agent.claude_tmux_events import MAX_EVENT_BYTES
from argos.services.agent.claude_tmux_session import QUESTION_INSTRUCTION
from argos.services.agent.tmux_native_events import parse_native_event, record_native


def main() -> None:
    """起動時に固定した所有先へ、必要な項目だけを保存する。"""
    parser = argparse.ArgumentParser(description="常駐Codex・agyのフック")
    parser.add_argument("directory", type=Path)
    parser.add_argument("generation")
    parser.add_argument("provider", choices=["codex", "antigravity"])
    parser.add_argument("event", choices=["SessionStart", "Stop", "Interrupt"])
    arguments = parser.parse_args()
    raw = sys.stdin.read(MAX_EVENT_BYTES + 1)
    if len(raw.encode()) > MAX_EVENT_BYTES:
        raise ValueError("フック入力が1MiBを超えています")
    event = parse_native_event(json.loads(raw), arguments.provider, arguments.event)
    record_native(arguments.directory, arguments.generation, arguments.provider, event)
    output: dict[str, object] = {}
    if arguments.event == "SessionStart":
        output = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": QUESTION_INSTRUCTION}}
        if arguments.provider == "antigravity":
            output = {"injectSteps": [{"systemMessage": {"systemMessage": QUESTION_INSTRUCTION}}]}
    print(json.dumps(output))


if __name__ == "__main__":
    main()
