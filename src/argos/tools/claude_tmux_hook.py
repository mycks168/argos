"""Claudeの試験スロット用フックを、ネットワークを使わず記録する。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from argos.services.agent.claude_tmux_events import MAX_EVENT_BYTES, record_event


def main() -> None:
    """stdinのフック入力を引数で指定された所有ファイルへ保存する。"""
    parser = argparse.ArgumentParser(description="Claude tmuxフックのローカル記録")
    parser.add_argument("path", type=Path)
    arguments = parser.parse_args()
    record_event(arguments.path, sys.stdin.read(MAX_EVENT_BYTES + 1))
    print("{}")


if __name__ == "__main__":
    main()
