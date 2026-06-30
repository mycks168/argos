#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""agyの`/usage`を実行し、モデルグループ別の使用率・リセット時刻をJSONで出力する。"""

import json
import os
import re
import sys
from datetime import datetime, timedelta

from tmux_util import cleanup, send_keys, tmux, wait_for

_LIMIT_RE = (
    r"{label}\s*\n\s*\[[^\]]*\]\s*([\d.]+)%\s*\n\s*"
    r"(?:(\d+)% remaining(?:\s*·\s*Refreshes in\s*(?:(\d+)h)?\s*(?:(\d+)m)?)?"
    r"|Quota available)"
)


def _parse_limit(block, label, now):
    """1つのグループ内の1つの上限(Weekly/Five Hour)を解析する。"""
    pattern = re.compile(_LIMIT_RE.format(label=re.escape(label)))
    m = pattern.search(block)
    if not m:
        raise ValueError(f"{label}を解析できませんでした:\n{block}")

    bar_pct = float(m.group(1))
    usage_pct = round(100 - bar_pct, 2)

    hours_str, minutes_str = m.group(3), m.group(4)
    if hours_str is None and minutes_str is None:
        reset = None
    else:
        delta = timedelta(hours=int(hours_str or 0), minutes=int(minutes_str or 0))
        reset = (now + delta).strftime("%m/%d %H:%M")

    return {"usage_pct": usage_pct, "reset": reset}


def parse_usage(screen, now=None):
    """`/usage`の画面テキストからグループ別の使用率・リセット時刻を抽出する。"""
    now = now or datetime.now()

    if "CLAUDE AND GPT MODELS" not in screen:
        raise ValueError(f"/usageの出力を解析できませんでした:\n{screen}")
    split_idx = screen.index("CLAUDE AND GPT MODELS")
    gemini_block, claude_block = screen[:split_idx], screen[split_idx:]

    return {
        "gemini": {
            "weekly": _parse_limit(gemini_block, "Weekly Limit", now),
            "five_hour": _parse_limit(gemini_block, "Five Hour Limit", now),
        },
        "claude_gpt": {
            "weekly": _parse_limit(claude_block, "Weekly Limit", now),
            "five_hour": _parse_limit(claude_block, "Five Hour Limit", now),
        },
    }


def main():
    session = f"agy_usage_{os.getpid()}"
    tmux("new-session", "-d", "-s", session, "-x", "220", "-y", "50", "agy")
    try:
        wait_for(session, lambda t: "for shortcuts" in t)

        send_keys(session, "/usage", "Enter")
        screen = wait_for(
            session, lambda t: "CLAUDE AND GPT MODELS" in t and "Five Hour Limit" in t
        )

        result = parse_usage(screen)
    finally:
        cleanup(session)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)
