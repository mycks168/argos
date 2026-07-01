#!/usr/bin/env python3
"""tenki.jp から台風詳細情報を取得し、日本地図上にプロットした台風情報HTMLを生成するスクリプト。"""

import argparse
import json
import os
import re
import urllib.request
from pathlib import Path

DEFAULT_HTML_URL_TEMPLATE = "https://tenki.jp/bousai/typhoon/{tid}/"
FALLBACK_TOP_URL = "https://tenki.jp/bousai/typhoon/"
SAVE_FILENAME = "typhoon_map.html"


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


def get_save_path(env_vars):
    """台風HTMLの保存先パスを決定する。"""
    static_dir = os.environ.get("ARGOS_DASHBOARD_STATIC_DIR") or env_vars.get("ARGOS_DASHBOARD_STATIC_DIR")
    if static_dir:
        return Path(static_dir).expanduser() / SAVE_FILENAME

    argos_home = os.environ.get("ARGOS_HOME") or env_vars.get("ARGOS_HOME") or "/opt/argos"
    return Path(argos_home).expanduser() / "src" / "argos" / "services" / "dashboard" / "static" / SAVE_FILENAME


def fetch_latest_typhoon_id():
    """tenki.jp 台風トップから最新の台風ID（例: 2608）を取得する。"""
    try:
        req = urllib.request.Request(
            FALLBACK_TOP_URL,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as res:
            html = res.read().decode("utf-8")
        typhoon_ids = re.findall(r'typhoon/([0-9]+)/', html)
        if typhoon_ids:
            return sorted(list(set(typhoon_ids)), key=int, reverse=True)[0]
    except Exception as e:
        print(f"Failed to fetch latest typhoon ID: {e}")
    return "2608"  # デフォルトフォールバック


def parse_typhoon_points(tid):
    """台風詳細ページから実況と予報の緯度経度をパースする。"""
    url = DEFAULT_HTML_URL_TEMPLATE.format(tid=tid)
    points = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as res:
            html = res.read().decode("utf-8")

        # 中心位置または予報円の中心をパースする
        pattern = r'(中心位置|予報円の中心)</th><td[^>]*>北緯\s*([0-9]+)度([0-9]+)分<br>東経\s*([0-9]+)度([0-9]+)分'
        matches = re.findall(pattern, html)

        for i, match in enumerate(matches):
            label_type, lat_d, lat_m, lon_d, lon_m = match
            lat = float(lat_d) + float(lat_m) / 60.0
            lon = float(lon_d) + float(lon_m) / 60.0

            if i == 0:
                label = "実況"
            else:
                label = f"予想({i*12}時間後)" if i <= 2 else "予想"

            points.append({
                "label": label,
                "lat": lat,
                "lon": lon,
                "is_current": (i == 0)
            })
    except Exception as e:
        print(f"Failed to parse typhoon points: {e}")
    return points


def latlon_to_css(lat, lon):
    """緯度経度を日本地図上の top/left パーセンテージに変換する。"""
    # 線形マッピング
    top = -4.88 * lat + 232.0
    left = 4.63 * lon - 584.0
    return max(0.0, min(100.0, top)), max(0.0, min(100.0, left))


def generate_html(tid, points):
    """日本地図上にプロットされた台風進路地図のHTMLを生成する。"""
    markers = []
    svg_elements = []
    line_coords = []

    for pt in points:
        top, left = latlon_to_css(pt["lat"], pt["lon"])
        line_coords.append((left, top))

        marker_class = "marker-current" if pt["is_current"] else "marker-forecast"
        color = "#ef4444" if pt["is_current"] else "#fbbf24"

        marker = f"""
        <div class="marker {marker_class}" style="top: {top}%; left: {left}%;">
            <div class="pin" style="background-color: {color};"></div>
            <div class="pulse" style="border-color: {color};"></div>
            <span class="label">{pt["label"]}</span>
        </div>
        """
        markers.append(marker)

        # 予報円を破線で描く
        if not pt["is_current"]:
            svg_elements.append(f'<circle cx="{left}" cy="{top}" r="4" stroke="#fbbf24" stroke-dasharray="1 0.5" stroke-width="0.3" fill="none" />')

    # 進路線を引く
    if len(line_coords) > 1:
        points_str = " ".join([f"{x},{y}" for x, y in line_coords])
        svg_elements.append(f'<polyline points="{points_str}" stroke="#ef4444" stroke-width="0.5" stroke-dasharray="1 0.5" fill="none" />')

    markers_html = "\n".join(markers)
    svg_html = "\n".join(svg_elements)

    t_num = int(tid[2:]) if len(tid) >= 4 else tid

    html_content = f"""<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <title>台風第{t_num}号 進路予測</title>
    <style>
        body {{
            margin: 0;
            padding: 0;
            background: #111827;
            color: #ffffff;
            font-family: 'Helvetica Neue', Arial, sans-serif;
            overflow: hidden;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
        }}
        .container {{
            position: relative;
            width: 90vw;
            height: 90vh;
            max-width: 600px;
            max-height: 600px;
            aspect-ratio: 1 / 1;
            background-image: url('https://upload.wikimedia.org/wikipedia/commons/thumb/a/af/Map_of_Japan_blank_blue.svg/640px-Map_of_Japan_blank_blue.svg.png');
            background-size: contain;
            background-repeat: no-repeat;
            background-position: center;
            border-radius: 16px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.5), inset 0 0 40px rgba(0,0,0,0.6);
            border: 1px solid rgba(255, 255, 255, 0.1);
            background-color: #1e293b;
        }}
        .header {{
            position: absolute;
            top: 20px;
            left: 20px;
            z-index: 10;
            background: rgba(15, 23, 42, 0.8);
            padding: 8px 16px;
            border-radius: 8px;
            border: 1px solid rgba(255, 255, 255, 0.15);
            backdrop-filter: blur(4px);
        }}
        .header h1 {{
            margin: 0;
            font-size: 16px;
            font-weight: bold;
            color: #f8fafc;
        }}
        .marker {{
            position: absolute;
            transform: translate(-50%, -50%);
            z-index: 5;
            display: flex;
            flex-direction: column;
            align-items: center;
        }}
        .pin {{
            width: 10px;
            height: 10px;
            border-radius: 50%;
            box-shadow: 0 0 8px rgba(0,0,0,0.5);
        }}
        .pulse {{
            position: absolute;
            width: 10px;
            height: 10px;
            border: 1px solid;
            border-radius: 50%;
            animation: pulse-animation 1.5s infinite ease-out;
            opacity: 0;
        }}
        .marker-current .pin {{
            width: 12px;
            height: 12px;
            background-color: #ef4444;
            animation: pulse-red 1s infinite alternate;
        }}
        .marker-current .pulse {{
            width: 12px;
            height: 12px;
            border-color: #ef4444;
        }}
        .label {{
            font-size: 10px;
            font-weight: bold;
            color: #f1f5f9;
            background: rgba(15, 23, 42, 0.75);
            padding: 2px 4px;
            border-radius: 4px;
            margin-top: 4px;
            white-space: nowrap;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }}
        @keyframes pulse-animation {{
            0% {{ transform: scale(1); opacity: 1; }}
            100% {{ transform: scale(3.5); opacity: 0; }}
        }}
        @keyframes pulse-red {{
            from {{ box-shadow: 0 0 4px #ef4444; }}
            to {{ box-shadow: 0 0 12px #f87171; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>台風第{t_num}号 進路予測マップ</h1>
        </div>
        <svg viewBox="0 0 100 100" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; z-index: 2;">
            {svg_html}
        </svg>
        {markers_html}
    </div>
</body>
</html>
"""
    return html_content


def send_overlay_event(dashboard_url, dashboard_token, target_slot, title="台風進路予測"):
    """ダッシュボードにHTML表示イベントを送信する。"""
    payload = {
        "type": "overlay",
        "overlay_type": "html",
        "title": title,
        "target_slot": target_slot,
        "url": "/static/typhoon_map.html"
    }

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
    parser = argparse.ArgumentParser(description="台風進路マップHTMLを生成し表示します。")
    parser.add_argument("--target-slot", choices=["center", "right"], default="center",
                        help="対象スロット（デフォルトは center）")
    parser.add_argument("--typhoon-id", help="対象の台風ID。省略時は自動で最新の台風を選択")
    parser.add_argument("--dry-run", action="store_true", help="イベント送信せずHTML生成のみ")
    args = parser.parse_args(argv)

    env_vars = load_env_vars()
    dashboard_token = os.environ.get("ARGOS_DASHBOARD_TOKEN") or env_vars.get("ARGOS_DASHBOARD_TOKEN")
    dashboard_host = os.environ.get("ARGOS_DASHBOARD_HOST") or env_vars.get("ARGOS_DASHBOARD_HOST") or "127.0.0.1"
    dashboard_port = os.environ.get("ARGOS_DASHBOARD_PORT") or env_vars.get("ARGOS_DASHBOARD_PORT") or "8765"
    dashboard_url = f"http://{dashboard_host}:{dashboard_port}/api/events"

    tid = args.typhoon_id or fetch_latest_typhoon_id()
    print(f"Target Typhoon ID: {tid}")

    points = parse_typhoon_points(tid)
    if not points:
        print("No typhoon points parsed.")
        return 1

    html_content = generate_html(tid, points)

    save_path = get_save_path(env_vars)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_text(html_content, encoding="utf-8")
    print(f"Generated HTML and saved to {save_path}")

    if args.dry_run:
        return 0

    try:
        t_num = int(tid[2:]) if len(tid) >= 4 else tid
        res = send_overlay_event(dashboard_url, dashboard_token, args.target_slot, title=f"台風第{t_num}号予測マップ")
        print(f"Success: {res}")
        return 0
    except Exception as e:
        print(f"Failed to send overlay event: {e}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
