#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""claude の `/usage` を実行し、使用率・リセット時刻をJSONで出力するスクリプト。"""

import json
import os
import re
import shutil
import sys
import time
from datetime import datetime

from tmux_util import capture, cleanup, send_keys, tmux, wait_for


def _extract_section(text: str, header: str, next_header: str) -> str:
    """テキストから特定のヘッダー間のブロックを切り出す。"""
    start = text.find(header)
    if start == -1:
        return ""
    end = text.find(next_header, start)
    if end == -1:
        return text[start:]
    return text[start:end]


def _parse_pct(block: str) -> float:
    """ブロックから使用パーセンテージをパースする。"""
    m = re.search(r"([\d.]+)%\s*used", block)
    return float(m.group(1)) if m else 0.0


def _parse_reset(block: str, now: datetime) -> str | None:
    """ブロックからリセット日時をパースして 'MM/DD HH:MM' フォーマットに変換する。"""
    m = re.search(r"Resets\s+([^\n]+)", block)
    if not m:
        return None

    raw_time = m.group(1).split("(")[0].strip()  # "11:49am" や "Jun 28, 2pm" など

    # パターン1: "11:49am" / "2:30pm" などの時刻のみ
    m_hm = re.match(r"(\d+):(\d+)\s*(am|pm)", raw_time, re.IGNORECASE)
    if m_hm:
        h = int(m_hm.group(1))
        m_val = int(m_hm.group(2))
        ampm = m_hm.group(3).lower()
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        dt = now.replace(hour=h, minute=m_val, second=0, microsecond=0)
        return dt.strftime("%m/%d %H:%M")

    # パターン2: "2pm" / "11am" などの分なし時刻のみ
    m_h = re.match(r"(\d+)\s*(am|pm)", raw_time, re.IGNORECASE)
    if m_h:
        h = int(m_h.group(1))
        ampm = m_h.group(2).lower()
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        dt = now.replace(hour=h, minute=0, second=0, microsecond=0)
        return dt.strftime("%m/%d %H:%M")

    # パターン3: "Jun 28, 2pm" / "Jun 28, 11:30am" などの日付＋時刻
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    m_date = re.match(r"([a-zA-Z]+)\s*(\d+),\s*(\d+)(?::(\d+))?\s*(am|pm)", raw_time, re.IGNORECASE)
    if m_date:
        mon_str = m_date.group(1).lower()[:3]
        if mon_str in months:
            month = months.index(mon_str) + 1
        else:
            month = now.month
        day = int(m_date.group(2))
        h = int(m_date.group(3))
        m_val = int(m_date.group(4) or 0)
        ampm = m_date.group(5).lower()
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        dt = now.replace(month=month, day=day, hour=h, minute=m_val, second=0, microsecond=0)
        return dt.strftime("%m/%d %H:%M")

    return raw_time


def parse_usage(screen: str, now: datetime | None = None) -> dict:
    """`/usage`の画面テキストから使用率とリセット時刻を抽出する。

    Args:
        screen: 画面全体のテキストキャプチャ
        now: 現在日時（テスト用）
    Returns:
        解析結果を格納した辞書
    """
    now = now or datetime.now()

    if "Current session" not in screen or "Current week" not in screen:
        raise ValueError(f"/usageの出力を解析できませんでした:\n{screen}")

    session_block = _extract_section(screen, "Current session", "Current week")
    week_block = _extract_section(screen, "Current week", "Usage credits")

    return {
        "weekly": {
            "usage_pct": _parse_pct(week_block),
            "reset": _parse_reset(week_block, now)
        },
        "five_hour": {
            "usage_pct": _parse_pct(session_block),
            "reset": _parse_reset(session_block, now)
        },
    }


def main() -> None:  # pragma: no cover
    """tmuxセッション上でclaudeを起動して/usageを実行し、結果をJSONで標準出力する。"""
    session = f"claude_usage_{os.getpid()}"
    claude_command = os.environ.get("CLAUDE_COMMAND") or shutil.which("claude")
    if not claude_command:
        raise RuntimeError("claudeコマンドが見つかりません")

    # 環境ごとにインストール先が違うため、PATHまたはCLAUDE_COMMANDで解決する
    tmux("new-session", "-d", "-s", session, "-x", "220", "-y", "50", claude_command)
    try:
        # 起動完了または信頼チェックダイアログを待つ
        try:
            screen = wait_for(
                session,
                lambda t: "shortcuts" in t or "❯" in t or "trust" in t or "Yes" in t,
                timeout=30
            )
        except Exception as e:
            print("--- Timeout during Launch ---", file=sys.stderr)
            try:
                print(capture(session), file=sys.stderr)
            except Exception:
                pass
            raise e

        # 信頼チェックが出た場合は承認する
        if "trust" in screen or "Yes" in screen:
            send_keys(session, "Enter")
            try:
                wait_for(session, lambda t: "shortcuts" in t or "❯" in t, timeout=30)
            except Exception as e:
                print("--- Timeout after Trust Approval ---", file=sys.stderr)
                try:
                    print(capture(session), file=sys.stderr)
                except Exception:
                    pass
                raise e

        # /usage コマンドを送信
        time.sleep(1.0)
        tmux("send-keys", "-t", session, "-l", "/usage")
        send_keys(session, "Enter")
        
        # 結果が表示されるまで待つ
        try:
            screen = wait_for(
                session, lambda t: "Current session" in t and "Current week" in t,
                timeout=30
            )
        except Exception as e:
            print("--- Timeout waiting for /usage output ---", file=sys.stderr)
            try:
                print(capture(session), file=sys.stderr)
            except Exception:
                pass
            raise e

        result = parse_usage(screen)
    finally:
        cleanup(session)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":  # pragma: no cover
    try:
        main()
    except Exception as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)
