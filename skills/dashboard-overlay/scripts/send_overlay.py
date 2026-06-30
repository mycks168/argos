#!/usr/bin/env python3
"""ARGOSダッシュボードにオーバーレイ表示を送信するスクリプト。"""

import argparse
import json
import os
import urllib.request
import urllib.parse
from pathlib import Path

MAP_PRESETS = {
    "tsuruoka-roadstations": {
        "title": "鶴岡方面 道の駅",
        "target_slot": "right",
        "zoom": 10,
        "zoom_offset": 1,
        "label_mode": "popup",
        "points": [
            "38.2174734,140.0976128,白鷹ヤナ公園",
            "38.32733,140.16911,あさひまち",
            "38.3819,140.2027,おおえ",
            "38.398948,140.271135,寒河江",
            "38.43608,140.09922,にしかわ",
            "38.586999,139.874491,月山",
            "38.7820288,139.8469164,庄内みかわ",
            "38.7625,139.9975,しょうない",
        ],
    },
}


def load_env_vars():
    """/home/yuki/argos/.env ファイルから設定値をロードする。"""
    env_path = Path("/home/yuki/argos/.env")
    env_vars = {}
    if env_path.exists():
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


def fetch_current_location(dashboard_host, dashboard_port):
    """ARGOSダッシュボードAPIから現在地を取得する。"""
    url = f"http://{dashboard_host}:{dashboard_port}/api/location"
    with urllib.request.urlopen(url, timeout=3) as res:
        payload = json.loads(res.read().decode("utf-8"))
    if not payload.get("available"):
        raise RuntimeError(f"現在地を取得できません: {payload}")
    return float(payload["lat"]), float(payload["lng"])


def apply_preset(args):
    """地図プリセットをCLI引数へ反映する。"""
    if not args.preset:
        return
    preset = MAP_PRESETS[args.preset]
    args.type = "map"
    args.target_slot = args.target_slot or preset["target_slot"]
    args.title = args.title or preset["title"]
    args.zoom = args.zoom if args.zoom != 13 else preset["zoom"]
    args.zoom_offset = args.zoom_offset or preset["zoom_offset"]
    args.label_mode = args.label_mode if args.label_mode != "permanent" else preset["label_mode"]
    if not args.point:
        args.point = list(preset["points"])


def build_payload(args):
    """CLI引数からARGOSダッシュボードイベントpayloadを組み立てる。"""
    if args.type == "clear":
        return {
            "type": "clear_overlay",
            "target_slot": args.target_slot
        }
    if args.type == "swap":
        return {
            "type": "swap_slots"
        }

    payload = {
        "type": "overlay",
        "overlay_type": args.type,
        "title": args.title or "表示",
        "target_slot": args.target_slot
    }

    # typeごとのパラメータ組み立て
    if args.type == "map":
        title_esc = urllib.parse.quote(args.title or "目的地", safe='')
        map_url = f"/static/map.html?zoom={args.zoom}&title={title_esc}"
        options = {"zoom": args.zoom}

        if args.zoom_offset:
            map_url += f"&zoom_offset={args.zoom_offset}"
            options["zoom_offset"] = args.zoom_offset
        if args.label_mode != "permanent":
            map_url += f"&label_mode={urllib.parse.quote(args.label_mode, safe='')}"
            options["label_mode"] = args.label_mode

        if args.point:
            points_str = "|".join(args.point)
            map_url += f"&points={urllib.parse.quote(points_str, safe='')}"
            options["points"] = args.point
        else:
            lat = args.lat or 35.6895
            lng = args.lng or 139.6917
            map_url += f"&lat={lat}&lng={lng}"
            options["lat"] = lat
            options["lng"] = lng
            if args.color:
                map_url += f"&color={urllib.parse.quote(args.color, safe='')}"
                options["color"] = args.color

        if args.cur_lat is not None and args.cur_lng is not None:
            map_url += f"&cur_lat={args.cur_lat}&cur_lng={args.cur_lng}"
            options["cur_lat"] = args.cur_lat
            options["cur_lng"] = args.cur_lng
        if args.follow_current:
            map_url += "&follow=1"
            options["follow_current"] = True
        if hasattr(args, "osrm_url") and args.osrm_url:
            map_url += f"&osrm_url={urllib.parse.quote(args.osrm_url, safe='')}"
            options["osrm_url"] = args.osrm_url
        if hasattr(args, "route") and args.route:
            map_url += "&route=1"
            options["route"] = True
        payload["url"] = map_url
        payload["options"] = options

    elif args.type == "nav":
        title_esc = urllib.parse.quote(args.title or "ナビ", safe='')
        orientation = args.orientation if args.orientation in {"north", "heading"} else "north"
        nav_url = f"/static/nav.html?zoom={args.zoom}&title={title_esc}&orientation={orientation}&interval={args.interval_ms}"
        payload["url"] = nav_url
        payload["replace_top"] = True
        payload["options"] = {
            "zoom": args.zoom,
            "orientation": orientation,
            "interval_ms": args.interval_ms,
        }

    elif args.type == "markdown":
        content = args.content or ""
        if args.file:
            file_path = Path(args.file)
            if not file_path.exists():
                raise FileNotFoundError(f"ファイルが見つかりません: {args.file}")
            content = file_path.read_text(encoding="utf-8")
        payload["content"] = content
        payload["url"] = args.url or "/static/reader.html"

    elif args.type == "image":
        image_url = args.url
        if not image_url:
            raise ValueError("--type image の場合は --url (画像パス) が必須です。")
        payload["url"] = f"/static/viewer.html?url={urllib.parse.quote(image_url, safe='')}"

    elif args.type == "html":
        if not args.url:
            raise ValueError("--type html の場合は --url が必須です。")
        payload["url"] = args.url
        if args.content:
            payload["content"] = args.content

    return payload


def send_payload(payload, dashboard_url, dashboard_token):
    """ARGOSダッシュボードAPIへpayloadを送信する。"""
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

    with urllib.request.urlopen(req) as res:
        return res.read().decode("utf-8")


def main(argv=None):
    """CLIエントリポイント。"""
    parser = argparse.ArgumentParser(description="ARGOSダッシュボードにオーバーレイイベントを送信します。")
    parser.add_argument("-t", "--type", choices=["map", "nav", "markdown", "image", "html", "clear", "swap"],
                        help="表示タイプ（clearで非表示、swapで左右スワップ）")
    parser.add_argument("--target-slot", choices=["center", "right"], default="right",
                        help="対象スロット（center または right、デフォルトは right）")
    parser.add_argument("--preset", choices=sorted(MAP_PRESETS),
                        help="よく使う地図プリセット")
    parser.add_argument("--title", help="表示タイトル")
    parser.add_argument("--url", help="コンテンツURL（または画像・HTMLのパス）")
    parser.add_argument("--content", help="直接渡すテキストデータ（Markdownなど）")
    parser.add_argument("-f", "--file", help="読み込むローカルファイルパス（Markdown等用）")
    parser.add_argument("--lat", type=float, help="緯度（単一地図用）")
    parser.add_argument("--lng", type=float, help="経度（単一地図用）")
    parser.add_argument("--color", help="マーカー色（単一地図用、例: #ff9900）")
    parser.add_argument("--zoom", type=int, default=13, help="ズームレベル（地図用、デフォルト13）")
    parser.add_argument("--orientation", choices=["north", "heading"], default="north",
                        help="ナビ地図の向き（north=北上、heading=進行方向上）")
    parser.add_argument("--interval-ms", type=int, default=2000,
                        help="ナビ地図の現在地更新間隔ミリ秒（デフォルト2000）")
    parser.add_argument("--zoom-offset", type=int, default=0, help="自動ズーム後の追加ズームレベル（地図用、デフォルト0）")
    parser.add_argument("--label-mode", choices=["permanent", "hover", "popup"], default="permanent",
                        help="地図ラベルの表示方法（permanent=常時表示、hover=ホバー時、popup=タップ時のみ）")
    parser.add_argument("--cur-lat", type=float, help="現在地の緯度（地図の現在地表示用）")
    parser.add_argument("--cur-lng", type=float, help="現在地の経度（地図の現在地表示用）")
    parser.add_argument("--current-location", action="store_true",
                        help="ARGOSの /api/location から現在地を取得して地図に含める")
    parser.add_argument("--no-follow-current", dest="follow_current", action="store_false",
                        help="地図表示後に現在地マーカーを自動追従させない")
    parser.set_defaults(follow_current=True)
    parser.add_argument("-p", "--point", action="append", help="複数目的地リスト（書式: lat,lng,title,color。colorは任意）。複数指定可能。")
    parser.add_argument("--route", action="store_true", help="目的地間（および現在地）の道路沿いのルートを描画する")
    parser.add_argument("--osrm-url", help="OSRMサーバーのURL（デフォルトは.envのOSRM_URL）")
    parser.add_argument("--dry-run", action="store_true", help="HTTP送信せずpayload JSONだけ表示する")
    
    args = parser.parse_args(argv)
    
    # 環境変数の読み込み
    env_vars = load_env_vars()
    dashboard_token = os.environ.get("ARGOS_DASHBOARD_TOKEN") or env_vars.get("ARGOS_DASHBOARD_TOKEN")
    
    # OSRM_URLのデフォルト設定
    if not args.osrm_url:
        args.osrm_url = os.environ.get("OSRM_URL") or env_vars.get("OSRM_URL")
    
    # デフォルトのURL設定
    dashboard_host = os.environ.get("ARGOS_DASHBOARD_HOST") or env_vars.get("ARGOS_DASHBOARD_HOST") or "127.0.0.1"
    dashboard_port = os.environ.get("ARGOS_DASHBOARD_PORT") or env_vars.get("ARGOS_DASHBOARD_PORT") or "8765"
    dashboard_url = f"http://{dashboard_host}:{dashboard_port}/api/events"
    
    try:
        apply_preset(args)
        if not args.type:
            raise ValueError("--type または --preset を指定してください")
        if args.current_location:
            args.cur_lat, args.cur_lng = fetch_current_location(dashboard_host, dashboard_port)
        payload = build_payload(args)
        if args.dry_run:
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        response_body = send_payload(payload, dashboard_url, dashboard_token)
        print(f"Success: {response_body}")
        return 0
    except Exception as e:
        print(f"Failed to send overlay event: {e}")
        return 1

if __name__ == "__main__":
    import sys
    sys.exit(main())
