#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""codexの`/status`を実行し、使用率・リセット時刻・クレジットをJSONで出力する。"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

from tmux_util import cleanup, send_keys, tmux, wait_for


def parse_limit_row(pattern_label, screen, now):
    """5h limit または Weekly limit の行を解析する。"""
    pattern = re.compile(
        re.escape(pattern_label) +
        r":\s*\[[^\]]*\]\s*(\d+)% left \(resets (\d{2}):(\d{2})(?: on (\d{1,2}) (\w{3}))?\)"
    )
    m = pattern.search(screen)
    if not m:
        return None
    left = int(m.group(1))
    hour = int(m.group(2))
    minute = int(m.group(3))

    if m.group(4) and m.group(5):
        day = int(m.group(4))
        mon = m.group(5)
        reset = datetime.strptime(f"{day} {mon} {now.year}", "%d %b %Y").replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if reset < now:
            reset = reset.replace(year=now.year + 1)
    else:
        reset = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if reset <= now:
            reset += timedelta(days=1)

    return {
        "usage_pct": 100 - left,
        "reset": reset.strftime("%m/%d %H:%M"),
    }


def parse_status(screen, now=None):
    """`/status`の画面テキストから使用率・リセット時刻・クレジットを抽出する。"""
    now = now or datetime.now()

    five_hour = parse_limit_row("5h limit", screen, now)
    weekly = parse_limit_row("Weekly limit", screen, now)
    mc = re.search(r"Credits:\s*([\d,]+) credits", screen)
    if not (weekly and mc):
        raise ValueError(f"/statusの出力を解析できませんでした:\n{screen}")

    if not five_hour:
        five_hour = {
            "usage_pct": 0,
            "reset": "N/A",
        }

    return {
        "five_hour": five_hour,
        "weekly": weekly,
        "credits": int(mc.group(1).replace(",", "")),
    }


def run_once():
    """1回 tmux セッションを立ち上げて /status を取得する。"""
    session = f"codex_status_{os.getpid()}_{int(time.time())}"
    tmux("new-session", "-d", "-s", session, "-x", "220", "-y", "50", "codex")
    try:
        screen = wait_for(
            session,
            lambda t: "Do you trust" in t or "OpenAI Codex" in t or "Update available" in t,
            timeout=60
        )
        if "Update available" in screen:
            time.sleep(1.0)
            send_keys(session, "2", "Enter")
            screen = wait_for(
                session,
                lambda t: "Do you trust" in t or "OpenAI Codex" in t,
                timeout=60
            )

        if "Do you trust" in screen:
            time.sleep(1.0)  # キー入力の取りこぼしを防ぐためのウェイト
            send_keys(session, "Enter")
            screen = wait_for(session, lambda t: "OpenAI Codex" in t, timeout=60)

        time.sleep(1.0)  # プロンプトがキー入力を受け付けられるようにするためのウェイト
        send_keys(session, "/status")
        # 補完候補(ドロップダウン)が表示されるまで待ってからEnterで選択する。
        # 表示前にEnterを送ると"/status"という文字列がチャットへの指示として
        # 送信されてしまい、応答待ちでタイムアウトする。
        wait_for(session, lambda t: "show current session configuration" in t, timeout=60)
        time.sleep(1.0)  # 補完選択を確実にするためのウェイト
        send_keys(session, "Enter")
        screen = wait_for(
            session,
            lambda t: "Credits:" in t or "refresh requested" in t,
            timeout=60
        )
        if "refresh requested" in screen:
            raise RuntimeError("refresh requested")

        return parse_status(screen)
    finally:
        cleanup(session)


def main():
    last_error = None
    for attempt in range(5):
        try:
            result = run_once()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        except Exception as e:
            last_error = e
            time.sleep(2)
    
    print(f"エラー: 複数回試行しましたが失敗しました。最後のエラー: {last_error}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
