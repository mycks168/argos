"""Codex・agyの完了通知をネットワークなしで記録する。"""

import argparse
import json
import sys
from pathlib import Path

from argos.services.agent.claude_tmux_events import MAX_EVENT_BYTES
from argos.services.agent.claude_tmux_session import QUESTION_INSTRUCTION
from argos.services.agent.tmux_native_events import parse_native_event, record_native
from argos.services.agent.subagent_hooks import record_subagent
from argos.services.agent.agy_subagents import record_agy_child_event


def main() -> None:
    """起動時に固定した所有先へ、必要な項目だけを保存する。"""
    parser = argparse.ArgumentParser(description="常駐Codex・agyのフック")
    parser.add_argument("directory", type=Path)
    parser.add_argument("generation")
    parser.add_argument("provider", choices=["codex", "antigravity"])
    parser.add_argument("event", choices=["SessionStart", "Stop", "Interrupt", "SubagentStop", "SubagentLink"])
    arguments = parser.parse_args()
    raw = sys.stdin.read(MAX_EVENT_BYTES + 1)
    if len(raw.encode()) > MAX_EVENT_BYTES:
        raise ValueError("フック入力が1MiBを超えています")
    payload = json.loads(raw)
    if arguments.provider == "antigravity" and record_agy_child_event(arguments.directory, arguments.generation, arguments.event, payload):
        pass
    elif arguments.event == "SubagentStop":
        record_subagent(arguments.directory, arguments.generation, payload)
    else:
        event = parse_native_event(payload, arguments.provider, arguments.event)
        record_native(arguments.directory, arguments.generation, arguments.provider, event)
    output: dict[str, object] = {}
    if arguments.event == "SessionStart":
        output = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": QUESTION_INSTRUCTION}}
        if arguments.provider == "antigravity":
            output = {"injectSteps": [{"systemMessage": {"systemMessage": QUESTION_INSTRUCTION}}]}
    print(json.dumps(output))


if __name__ == "__main__":
    main()
