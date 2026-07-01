#!/usr/bin/env python3
"""tenki.jp から最新の台風画像（日本近海、広域、または個別台風ズーム）をダウンロードして、ARGOSダッシュボードに送信するスクリプト。"""

import argparse
import json
import os
import re
import urllib.request
import urllib.parse
from pathlib import Path

DEFAULT_IMAGE_URL = "https://static.tenki.jp/static-images/typhoon-detail/recent/japan_near-large.jpg"
FALLBACK_HTML_URL = "https://tenki.jp/bousai/typhoon/"
SAVE_FILENAME = "typhoon.jpg"


def find_env_file():
    """実行環境に合わせてARGOSの.envファイルを探す。"""
    explicit_path = os.environ.get("ARGOS_ENV_FILE")
    candidates = []
    if explicit_path:
        candidates.append(Path(explicit_path).expanduser())
    candidates.append(Path.cwd() / ".env")
    candidates.extend(parent / ".env" for parent in Path(__file__).resolve().parents)
    candidates.append(Path("/opt/argos/.env"))

    seen = set()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        if candidate.is_file():
            return candidate
    return None


def load_env_vars():
    """ARGOSの.envファイルから設定値をロードする。"""
    env_path = find_env_file()
    env_vars = {}
    if env_path:
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, val = line.split("=", 1)
                        env_vars[key.strip()] = val.strip()
        except Exception:
            pass
    return env_vars


def get_static_dir(env_vars):
    """台風画像の保存先staticディレクトリを決定する。"""
    static_dir = os.environ.get("ARGOS_DASHBOARD_STATIC_DIR") or env_vars.get("ARGOS_DASHBOARD_STATIC_DIR")
    if static_dir:
        return Path(static_dir).expanduser()

    argos_home = os.environ.get("ARGOS_HOME") or env_vars.get("ARGOS_HOME") or "/opt/argos"
    return Path(argos_home).expanduser() / "src" / "argos" / "services" / "dashboard" / "static"


def get_image_url(mode, typhoon_id=None):
    """表示モードや台風IDに応じて画像URLを決定する。"""
    if mode == "wide":
        return "https://static.tenki.jp/static-images/typhoon-detail/recent/japan_wide-large.jpg"
    elif mode == "near":
        return "https://static.tenki.jp/static-images/typhoon-detail/recent/japan_near-large.jpg"

    # detail モード
    if typhoon_id:
        tid = typhoon_id
        if not tid.startswith("typhoon_"):
            tid = f"typhoon_{tid}"
        return f"https://static.tenki.jp/static-images/typhoon-detail/recent/{tid}-large.jpg"

    # 台風ID省略時はHTMLから最新の台風IDを自動検出する
    try:
        req = urllib.request.Request(
            FALLBACK_HTML_URL,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as res:
            html = res.read().decode("utf-8")

        typhoon_ids = re.findall(r'typhoon_([0-9]+)', html)
        if typhoon_ids:
            # 重複を排除し、最大（最新）のIDを選択
            unique_ids = sorted(list(set(typhoon_ids)), key=int, reverse=True)
            latest_id = unique_ids[0]
            print(f"Auto-detected latest typhoon ID: {latest_id}")
            return f"https://static.tenki.jp/static-images/typhoon-detail/recent/typhoon_{latest_id}-large.jpg"
    except Exception as e:
        print(f"Failed to auto-detect latest typhoon ID: {e}")

    return DEFAULT_IMAGE_URL


def download_image(url, dest_path):
    """画像をダウンロードして保存する。"""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=10) as res:
        data = res.read()

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(data)
    print(f"Downloaded: {url} -> {dest_path}")


def send_overlay_event(dashboard_url, dashboard_token, target_slot, title="台風情報"):
    """ダッシュボードに画像表示イベントを送信する。"""
    payload = {
        "type": "overlay",
        "overlay_type": "image",
        "title": title,
        "target_slot": target_slot,
        "url": f"/static/{SAVE_FILENAME}"
    }

    payload["url"] = f"/static/viewer.html?url={urllib.parse.quote(payload['url'], safe='')}"

    headers = {
        "Content-Type": "application/json"
    }
    if dashboard_token:
        headers["Authorization"] = f"Bearer {dashboard_token}"

    req = urllib.request.Request(
        dashboard_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=5) as res:
        return res.read().decode("utf-8")


def main(argv=None):
    """メインエントリーポイント。"""
    parser = argparse.ArgumentParser(description="台風画像をダウンロードしてダッシュボードに表示します。")
    parser.add_argument("--target-slot", choices=["center", "right"], default="center",
                        help="対象スロット（デフォルトは center）")
    parser.add_argument("--mode", choices=["near", "wide", "detail"], default="near",
                        help="表示モード（near: 日本近海, wide: 広域, detail: 個別台風ズーム。デフォルトは near）")
    parser.add_argument("--typhoon-id", help="表示したい特定の台風ID（例: 2608）。省略時は最新の台風を自動選択します。")
    parser.add_argument("--dry-run", action="store_true", help="HTTP送信せずダウンロードだけ実行")
    args = parser.parse_args(argv)

    env_vars = load_env_vars()
    dashboard_token = os.environ.get("ARGOS_DASHBOARD_TOKEN") or env_vars.get("ARGOS_DASHBOARD_TOKEN")
    dashboard_host = os.environ.get("ARGOS_DASHBOARD_HOST") or env_vars.get("ARGOS_DASHBOARD_HOST") or "127.0.0.1"
    dashboard_port = os.environ.get("ARGOS_DASHBOARD_PORT") or env_vars.get("ARGOS_DASHBOARD_PORT") or "8765"
    dashboard_url = f"http://{dashboard_host}:{dashboard_port}/api/events"

    # 1. 画像URLの決定
    img_url = get_image_url(args.mode, args.typhoon_id)
    
    # タイトル決定
    title = "台風情報"
    if args.mode == "near":
        title = "台風情報（日本近海）"
    elif args.mode == "wide":
        title = "台風情報（広域）"
    elif args.mode == "detail" or "typhoon_" in img_url:
        match = re.search(r'typhoon_([0-9]+)', img_url)
        tid = match.group(1) if match else (args.typhoon_id or "詳細")
        # 台風番号をわかりやすく（例: 2608 -> 8号）にする
        if len(tid) >= 4:
            num = int(tid[2:])
            title = f"台風第{num}号情報"
        else:
            title = f"台風{tid}情報"

    # 2. ダウンロードと保存
    dest = get_static_dir(env_vars) / SAVE_FILENAME
    try:
        download_image(img_url, dest)
    except Exception as e:
        print(f"Failed to download image from {img_url}: {e}")
        if img_url != DEFAULT_IMAGE_URL:
            print("Retrying with default recent URL...")
            try:
                download_image(DEFAULT_IMAGE_URL, dest)
                title = "台風情報（日本近海）"
            except Exception as re_err:
                print(f"Failed to download recent image as well: {re_err}")
                return 1
        else:
            return 1

    # 3. イベント送信
    if args.dry_run:
        print("Dry-run: Skipped sending event to dashboard.")
        return 0

    try:
        res = send_overlay_event(dashboard_url, dashboard_token, args.target_slot, title=title)
        print(f"Success: {res}")
        return 0
    except Exception as e:
        print(f"Failed to send overlay event: {e}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
