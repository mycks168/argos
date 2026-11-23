#!/usr/bin/env python3
"""気象庁 高解像度降水ナウキャスト（雨雲レーダー）をARGOSダッシュボードに表示する。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.append(str(Path(__file__).parent))
import send_overlay  # noqa: E402

# 現在地が取れず、設定（dashboard.rain_radar_default）もないときの地点。
# 既存の地図（map.html・nav.html）と同じ東京の座標にそろえる。利用者の地域はソースに書かない。
FALLBACK_LOCATION = (35.6895, 139.6917, "東京")
DEFAULT_ENV = "ARGOS_DASHBOARD_RAIN_RADAR_DEFAULT"


def parse_default_location(value: str) -> tuple[float, float, str] | None:
    """「緯度,経度,表示名」の文字列を読む。正しくなければ None。"""
    parts = [part.strip() for part in value.split(",", 2)]
    if len(parts) < 2:
        return None
    try:
        lat, lng = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    label = parts[2] if len(parts) > 2 and parts[2] else "既定の地点"
    return lat, lng, label


def default_location() -> tuple[float, float, str]:
    """現在地が取れないときの地点。環境変数 → config.yaml の dashboard.rain_radar_default → 東京 の順。"""
    value = os.environ.get(DEFAULT_ENV, "")
    if not value:
        try:
            from argos.yaml_config import load_yaml_environment

            value = load_yaml_environment().get(DEFAULT_ENV, "")
        except Exception:
            value = ""
    return parse_default_location(value) or FALLBACK_LOCATION
GSI_GEOCODE_URL = "https://msearch.gsi.go.jp/address-search/AddressSearch"


def geocode_place(query: str) -> tuple[float, float, str] | None:
    """国土地理院の住所検索APIで地名・駅名から緯度経度を解決する。"""
    q = query.strip()
    if not q:
        return None
    url = f"{GSI_GEOCODE_URL}?q={urllib.parse.quote(q, safe='')}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", "argos-rain-radar/1.0")
    with urllib.request.urlopen(req, timeout=5) as res:
        items = json.loads(res.read().decode("utf-8"))
    if not isinstance(items, list) or not items:
        return None
    first = items[0]
    coords = first.get("geometry", {}).get("coordinates")
    if not isinstance(coords, list) or len(coords) < 2:
        return None
    lng, lat = float(coords[0]), float(coords[1])
    title = str(first.get("properties", {}).get("title") or q).strip()
    return lat, lng, title


def resolve_radar_target(
    *,
    lat: float | None,
    lng: float | None,
    query: str,
    label: str,
    dashboard_host: str,
    dashboard_port: str | int,
) -> tuple[float, float, str]:
    """明示座標 → 地名検索 → ARGOS現在地 → 既定の地点（設定、なければ東京）の順で表示位置を決める。"""
    if lat is not None and lng is not None:
        return float(lat), float(lng), label or "指定地点"
    if query.strip():
        found = geocode_place(query)
        if found is None:
            raise RuntimeError(f"指定された場所が見つかりません: {query}")
        return found[0], found[1], label or found[2]
    try:
        cur_lat, cur_lng = send_overlay.fetch_current_location(dashboard_host, dashboard_port)
        return cur_lat, cur_lng, label or "現在地"
    except Exception:
        fallback = default_location()
        return fallback[0], fallback[1], label or fallback[2]


def build_radar_url(lat: float, lng: float, label: str, zoom: int, play: bool) -> str:
    """rain_radar.html のクエリ付きURLを組み立てる。"""
    params = urllib.parse.urlencode(
        {
            "lat": f"{lat:.6f}",
            "lng": f"{lng:.6f}",
            "label": label,
            "zoom": str(zoom),
            "play": "1" if play else "0",
        }
    )
    return f"/static/rain_radar.html?{params}"


def main(argv: list[str] | None = None) -> int:
    """CLIエントリポイント。"""
    parser = argparse.ArgumentParser(description="雨雲レーダーをARGOSダッシュボードに表示します。")
    parser.add_argument("-q", "--query", default="", help="表示したい地名・駅名（省略時は現在地、取れなければ既定の地点）")
    parser.add_argument("--lat", type=float, default=None, help="中心の緯度")
    parser.add_argument("--lng", type=float, default=None, help="中心の経度")
    parser.add_argument("--label", default="", help="マーカーに表示するラベル")
    parser.add_argument("--zoom", type=int, default=10, help="初期ズームレベル（既定: 10）")
    parser.add_argument("--target-slot", choices=["center", "right"], default="center", help="表示先スロット")
    parser.add_argument("--title", default="", help="オーバーレイのタイトル")
    parser.add_argument("--no-play", action="store_true", help="自動再生をオフにして表示する")
    parser.add_argument("--dry-run", action="store_true", help="送信せずペイロードのみ出力する")
    args = parser.parse_args(argv)

    env_vars = send_overlay.load_env_vars()
    host = os.environ.get("ARGOS_DASHBOARD_HOST") or env_vars.get("ARGOS_DASHBOARD_HOST", "127.0.0.1")
    port = os.environ.get("ARGOS_DASHBOARD_PORT") or env_vars.get("ARGOS_DASHBOARD_PORT", "8765")
    token = os.environ.get("ARGOS_DASHBOARD_TOKEN") or env_vars.get("ARGOS_DASHBOARD_TOKEN", "")

    lat, lng, resolved_label = resolve_radar_target(
        lat=args.lat,
        lng=args.lng,
        query=args.query,
        label=args.label,
        dashboard_host=host,
        dashboard_port=port,
    )
    radar_url = build_radar_url(lat, lng, resolved_label, args.zoom, play=not args.no_play)
    payload = {
        "type": "overlay",
        "overlay_type": "html",
        "title": args.title or f"雨雲レーダー ({resolved_label})",
        "target_slot": args.target_slot,
        "url": radar_url,
    }

    if args.dry_run:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if not token:
        print("Error: ARGOS_DASHBOARD_TOKEN が設定されていません。", file=sys.stderr)
        return 1

    dashboard_url = f"http://{host}:{port}/api/events"
    send_overlay.send_payload(payload, dashboard_url, token)
    print(f"雨雲レーダーを {args.target_slot} に表示しました: {resolved_label} ({lat:.4f}, {lng:.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
