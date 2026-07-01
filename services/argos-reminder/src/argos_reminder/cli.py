"""argos-reminder CLI。"""

from __future__ import annotations

import argparse
from datetime import datetime

from argos_reminder.argos_client import ArgosClient
from argos_reminder.config import load_settings
from argos_reminder.runner import run_due_once
from argos_reminder.scheduler import create_location_reminder, create_reminder, now_local, parse_datetime
from argos_reminder.store import ReminderStore


def main(argv: list[str] | None = None) -> int:
    """CLIエントリポイント。"""
    parser = argparse.ArgumentParser(description="ARGOSへ日時指定リマインダー通知を送ります。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="リマインダーを追加する")
    add_parser.add_argument("when", help="通知日時。例: 2026-06-19 18:30")
    add_parser.add_argument("title", help="通知タイトル")
    add_parser.add_argument("--text", default="", help="通知本文")
    add_parser.add_argument("--source", default="Reminder", help="通知元")
    add_parser.add_argument("--no-sound", action="store_true", help="通知音を鳴らさない")
    add_parser.add_argument("--no-speak", action="store_true", help="読み上げない")

    location_parser = subparsers.add_parser("add-location", help="指定地点に近づいたら通知する")
    location_parser.add_argument("title", help="通知タイトル")
    location_parser.add_argument("--lat", type=float, required=True, help="目的地の緯度")
    location_parser.add_argument("--lon", type=float, required=True, help="目的地の経度")
    location_parser.add_argument("--radius-m", type=float, default=100.0, help="到着判定の半径メートル。既定は100")
    location_parser.add_argument("--text", default="", help="通知本文")
    location_parser.add_argument("--source", default="Reminder", help="通知元")
    location_parser.add_argument("--no-sound", action="store_true", help="通知音を鳴らさない")
    location_parser.add_argument("--no-speak", action="store_true", help="読み上げない")

    subparsers.add_parser("list", help="リマインダーを一覧表示する")

    remove_parser = subparsers.add_parser("remove", help="リマインダーを削除する")
    remove_parser.add_argument("id", help="削除するリマインダーID")

    subparsers.add_parser("run-once", help="期限到達済みリマインダーを送信する")

    args = parser.parse_args(argv)
    settings = load_settings()
    store = ReminderStore(settings.state_path)

    if args.command == "add":
        reminder = create_reminder(
            scheduled_at=parse_datetime(args.when),
            title=args.title,
            text=args.text,
            source=args.source,
            sound=not args.no_sound,
            speak=not args.no_speak,
        )
        store.add(reminder)
        print(f"追加しました: {reminder.id} {reminder.scheduled_at.isoformat()} {reminder.title}")
        return 0
    if args.command == "add-location":
        reminder = create_location_reminder(
            title=args.title,
            target_lat=args.lat,
            target_lon=args.lon,
            radius_m=args.radius_m,
            text=args.text,
            source=args.source,
            sound=not args.no_sound,
            speak=not args.no_speak,
        )
        store.add(reminder)
        print(f"追加しました: {reminder.id} {reminder.target_lat},{reminder.target_lon} 半径{reminder.radius_m:.0f}m {reminder.title}")
        return 0
    if args.command == "list":
        for reminder in store.load():
            status = "送信済み" if reminder.sent_at else "未送信"
            if reminder.kind == "location":
                target = f"{reminder.target_lat},{reminder.target_lon} 半径{reminder.radius_m:.0f}m"
            else:
                target = reminder.scheduled_at.isoformat() if reminder.scheduled_at else ""
            print(f"{reminder.id}\t{status}\t{target}\t{reminder.title}")
        return 0
    if args.command == "remove":
        if not store.remove(args.id):
            print("指定IDのリマインダーはありません")
            return 1
        print("削除しました")
        return 0
    if args.command == "run-once":
        client = ArgosClient(settings.dashboard_url, settings.dashboard_token)
        sent_count = run_due_once(store, client, now_local())
        print(f"送信件数: {sent_count}")
        return 0
    raise AssertionError("未対応コマンド")


if __name__ == "__main__":
    raise SystemExit(main())
