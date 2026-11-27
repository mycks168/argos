#!/usr/bin/env python3
"""ARGOSのタイマー。複数のタイマーを同時に動かし、ダッシュボードの右側に残り時間を出す。

時間を数えるのは systemd のユーザータイマー（systemd-run --user --on-active）。
タイマーごとに使い捨てのユニットを作るので、いくつでも同時に動き、ARGOSを再起動しても消えない。
時間になると systemd がこのスクリプトの fire を呼び、通知（音と読み上げ）を送って画面を更新する。

    argos_timer.py start --minutes 3 --label パスタ
    argos_timer.py list
    argos_timer.py cancel --label パスタ     （--label がなければ全部）
    argos_timer.py fire --id <id>            （systemd から呼ばれる）

標準出力の最後の行は、読み上げる短い文（高速振り分けの reply に使う）。
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Iterator

# このスクリプトは <ARGOS>/skills/timer/scripts/ にある。
ARGOS_ROOT = Path(__file__).resolve().parents[3]
STATE_PATH = Path(os.environ.get("ARGOS_TIMER_STATE", "~/.local/state/argos-timer/timers.json")).expanduser()
UNIT_PREFIX = "argos-timer-"
MAX_SECONDS = 24 * 60 * 60
MAX_LABEL = 20
MAX_TIMERS = 10


# --- 時間・名前 ----------------------------------------------------------------


def format_duration(seconds: int) -> str:
    """秒数を「1時間2分3秒」のような読み上げやすい形にする。"""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [f"{hours}時間" if hours else "", f"{minutes}分" if minutes else "", f"{secs}秒" if secs else ""]
    return "".join(parts) or "0秒"


def total_seconds(hours: int, minutes: int, seconds: int) -> int:
    """時・分・秒を合計し、1秒〜24時間に収まらなければ ValueError。"""
    if min(hours, minutes, seconds) < 0:
        raise ValueError("時間はマイナスにできません")
    total = hours * 3600 + minutes * 60 + seconds
    if not 1 <= total <= MAX_SECONDS:
        raise ValueError("タイマーは1秒から24時間までです")
    return total


def unique_label(label: str, used: set[str]) -> str:
    """名前がなければ「タイマー1」から空いている番号、同じ名前があれば「パスタ2」のように番号を付ける。"""
    label = label.strip()[:MAX_LABEL]
    if not label:
        number = 1
        while f"タイマー{number}" in used:
            number += 1
        return f"タイマー{number}"
    if label not in used:
        return label
    number = 2
    while f"{label}{number}" in used:
        number += 1
    return f"{label}{number}"


# --- 状態ファイル -------------------------------------------------------------


@contextlib.contextmanager
def locked_state(path: Path = STATE_PATH) -> Iterator[dict[str, Any]]:
    """状態ファイルを排他して読み、ブロックを抜けるときに書き戻す。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or not isinstance(state.get("timers"), list):
                raise ValueError
        except (OSError, ValueError):
            state = {"timers": [], "overlay_shown": False}
        yield state
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def active_timers(state: dict[str, Any], now: float) -> list[dict[str, Any]]:
    """まだ終わっていないタイマーを、終わる順に返す。終わったものは状態から外す。"""
    timers = [t for t in state["timers"] if float(t.get("ends_at", 0)) > now]
    state["timers"] = sorted(timers, key=lambda t: float(t["ends_at"]))
    return state["timers"]


# --- systemd ------------------------------------------------------------------


def _user_env() -> dict[str, str]:
    """systemctl --user に必要な環境変数をそろえる（サービスから呼ばれても使えるように）。"""
    env = dict(os.environ)
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    env.setdefault("XDG_RUNTIME_DIR", runtime)
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime}/bus")
    return env


def schedule(timer_id: str, seconds: int) -> None:
    """seconds 秒後に fire を呼ぶ使い捨てのユーザータイマーを作る。"""
    subprocess.run(
        [
            "systemd-run", "--user", "--quiet", "--collect",
            f"--unit={UNIT_PREFIX}{timer_id}",
            f"--on-active={seconds}s",
            "--timer-property=AccuracySec=1s",
            sys.executable, str(Path(__file__).resolve()), "fire", "--id", timer_id,
        ],
        check=True, env=_user_env(), capture_output=True, text=True, timeout=15,
    )


def unschedule(timer_id: str) -> None:
    """ユーザータイマーを止める。もう無ければ何もしない。"""
    subprocess.run(
        ["systemctl", "--user", "stop", f"{UNIT_PREFIX}{timer_id}.timer"],
        check=False, env=_user_env(), capture_output=True, text=True, timeout=15,
    )


# --- ダッシュボード -------------------------------------------------------------


def _dashboard() -> tuple[str, str]:
    """イベントAPIのURLとトークンを、環境変数、なければ ARGOS の config.yaml から読む。"""
    values: dict[str, str] = {}
    try:
        from argos.yaml_config import load_yaml_environment

        config = os.environ.get("ARGOS_CONFIG_FILE") or str(ARGOS_ROOT / "config.yaml")
        values = load_yaml_environment(Path(config).expanduser())
    except Exception:
        values = {}

    def get(name: str, default: str = "") -> str:
        """環境変数を優先して設定値を返す。"""
        return os.environ.get(name) or values.get(name) or default

    host = get("ARGOS_DASHBOARD_HOST", "127.0.0.1")
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    port = get("ARGOS_DASHBOARD_PORT", "8765")
    return f"http://{host}:{port}/api/events", get("ARGOS_DASHBOARD_TOKEN")


def post_event(payload: dict[str, Any]) -> None:
    """ダッシュボードへイベントを送る。"""
    url, token = _dashboard()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        response.read()


def timer_url(timers: list[dict[str, Any]]) -> str:
    """タイマー一覧ページ（timers.html）のURL。名前・終わる時刻（ミリ秒）・長さ（秒）を渡し、ページ側で数える。"""
    data = [{"l": t["label"], "e": int(float(t["ends_at"]) * 1000), "s": int(t["seconds"])} for t in timers]
    return "/static/timers.html?" + urllib.parse.urlencode({"t": json.dumps(data, ensure_ascii=False)})


def render(state: dict[str, Any], now: float) -> None:
    """動いているタイマーを右側に出し直す。無くなったら、出していたページを閉じる。"""
    timers = active_timers(state, now)
    try:
        if timers:
            post_event({
                "type": "overlay", "overlay_type": "html", "title": "タイマー",
                "url": timer_url(timers), "target_slot": "right",
                # 2回目以降は、出しているタイマーのページを差し替える（重ねない）。
                "replace_top": bool(state.get("overlay_shown")),
            })
            state["overlay_shown"] = True
        elif state.get("overlay_shown"):
            post_event({"type": "clear_overlay", "target_slot": "right"})
            state["overlay_shown"] = False
    except OSError as exc:
        print(f"画面を更新できませんでした: {exc}", file=sys.stderr)


# --- コマンド -------------------------------------------------------------------


def cmd_start(args: argparse.Namespace) -> str:
    """タイマーを始める。"""
    seconds = total_seconds(args.hours, args.minutes, args.seconds)
    now = time.time()
    with locked_state() as state:
        timers = active_timers(state, now)
        if len(timers) >= MAX_TIMERS:
            raise ValueError(f"タイマーは同時に{MAX_TIMERS}個までです")
        label = unique_label(args.label or "", {t["label"] for t in timers})
        timer_id = uuid.uuid4().hex[:12]
        schedule(timer_id, seconds)
        timers.append({"id": timer_id, "label": label, "seconds": seconds, "ends_at": now + seconds})
        render(state, now)
    return f"{label}、{format_duration(seconds)}のタイマーを始めたよ。"


def cmd_list(_args: argparse.Namespace) -> str:
    """動いているタイマーと残り時間を返す。"""
    now = time.time()
    with locked_state() as state:
        timers = active_timers(state, now)
    if not timers:
        return "動いているタイマーはないよ。"
    parts = [f"{t['label']}があと{format_duration(round(float(t['ends_at']) - now))}" for t in timers]
    return "、".join(parts) + "だよ。"


def cmd_cancel(args: argparse.Namespace) -> str:
    """名前が合うタイマー（名前がなければ全部）を止める。"""
    now = time.time()
    label = (args.label or "").strip()
    with locked_state() as state:
        timers = active_timers(state, now)
        targets = [t for t in timers if not label or t["label"] == label]
        if label and not targets:
            # 「パスタ」で「パスタ2」まで止めないよう、完全一致がなければ前方一致の1つだけ
            targets = [t for t in timers if t["label"].startswith(label)][:1]
        for timer in targets:
            unschedule(timer["id"])
        state["timers"] = [t for t in timers if t not in targets]
        render(state, now)
    if not targets:
        return f"{label}のタイマーは見つからなかったよ。" if label else "動いているタイマーはないよ。"
    if not label:
        return "タイマーを全部止めたよ。"
    return f"{targets[0]['label']}のタイマーを止めたよ。"


def cmd_fire(args: argparse.Namespace) -> str:
    """時間になったタイマーを知らせ、画面から消す（systemd から呼ばれる）。"""
    now = time.time()
    with locked_state() as state:
        timer = next((t for t in state["timers"] if t["id"] == args.id), None)
        state["timers"] = [t for t in state["timers"] if t["id"] != args.id]
        render(state, now)
    if timer is None:
        return "そのタイマーはもう止まっているよ。"
    # 通知の読み上げは「題名。本文」になるので、題名の最後に「。」を付けない。
    message = f"{timer['label']}のタイマーが終わったよ"
    post_event({
        "type": "notification", "title": message, "text": f"{format_duration(int(timer['seconds']))}たちました。",
        "source": "タイマー", "priority": "high", "sound": "alarm", "speak": True,
    })
    return message + "。"


def main(argv: list[str] | None = None) -> int:
    """コマンドの入口。最後の行に読み上げる文を出す。"""
    parser = argparse.ArgumentParser(description="ARGOSのタイマー")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="タイマーを始める")
    start.add_argument("--hours", type=int, default=0)
    start.add_argument("--minutes", type=int, default=0)
    start.add_argument("--seconds", type=int, default=0)
    start.add_argument("--label", default="")
    sub.add_parser("list", help="動いているタイマー")
    cancel = sub.add_parser("cancel", help="タイマーを止める（--label がなければ全部）")
    cancel.add_argument("--label", default="")
    fire = sub.add_parser("fire", help="時間になったタイマーを知らせる（systemd から呼ばれる）")
    fire.add_argument("--id", required=True)
    args = parser.parse_args(argv)
    handlers = {"start": cmd_start, "list": cmd_list, "cancel": cmd_cancel, "fire": cmd_fire}
    try:
        print(handlers[args.command](args))
    except (ValueError, subprocess.SubprocessError, OSError) as exc:
        print(f"タイマーを操作できませんでした: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
