#!/usr/bin/env python3
"""Slack Incoming Webhookへ短い通知を送信する。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


KIND_LABELS = {
    "info": "ARGOS通知",
    "warning": "ARGOS注意",
    "error": "ARGOSエラー",
    "task": "ARGOS作業結果",
    "link": "ARGOSリンク",
}


def build_payload(title: str, text: str, url: str = "", kind: str = "info", source: str = "ARGOS") -> dict[str, Any]:
    """Slack Webhookへ送るpayloadを作成する。"""
    kind = kind.strip() or "info"
    source = source.strip() or "ARGOS"
    title = title.strip()
    text = text.strip()
    url = url.strip()
    if not any((title, text, url)):
        raise ValueError("通知本文が空です")
    label = KIND_LABELS.get(kind, KIND_LABELS["info"])
    heading = title or label
    fallback = " ".join(part for part in (f"[{source}]", heading, text, url) if part).strip()
    if not title and not url:
        return {"text": fallback[:3000]}
    blocks: list[dict[str, Any]] = []
    blocks.append({"type": "header", "text": {"type": "plain_text", "text": f"{source}: {heading}"[:150]}})
    if text:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text[:3000]}})
    if url:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"<{url}|リンクを開く>"}})
    return {"text": fallback[:3000], "blocks": blocks}


def post_webhook(webhook_url: str, payload: dict[str, Any]) -> None:
    """Slack Incoming WebhookへpayloadをPOSTする。"""
    if not webhook_url.strip():
        raise ValueError("SLACK_WEBHOOK_URL が未設定です")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        if response.status >= 400:
            raise RuntimeError(f"Slack通知に失敗しました: HTTP {response.status}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    """コマンドライン引数を解析する。"""
    parser = argparse.ArgumentParser(description="Slackへ短い通知を送信します")
    parser.add_argument("--kind", default="info", choices=sorted(KIND_LABELS), help="通知種別")
    parser.add_argument("--source", default="ARGOS", help="通知元名")
    parser.add_argument("--title", default="", help="通知タイトル")
    parser.add_argument("--text", required=True, help="通知本文")
    parser.add_argument("--url", default="", help="開いてほしいURL")
    parser.add_argument("--dry-run", action="store_true", help="送信せずpayloadだけ表示します")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Slack通知CLIのエントリーポイント。"""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        payload = build_payload(args.title, args.text, args.url, args.kind, args.source)
        if args.dry_run:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        post_webhook(os.environ.get("SLACK_WEBHOOK_URL", ""), payload)
    except (ValueError, RuntimeError, HTTPError, URLError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    print("Slack通知を送信しました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
