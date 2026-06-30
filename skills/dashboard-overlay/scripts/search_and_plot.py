#!/usr/bin/env python3
"""名前で場所を検索し、ARGOSダッシュボードにプロットするスクリプト。"""

import argparse
import sys
import json
import os
import csv
import urllib.request
import urllib.parse
from pathlib import Path

# 同一ディレクトリの send_overlay をインポートするため sys.path を調整
sys.path.append(str(Path(__file__).parent))
import send_overlay

OVERPASS_URL = "https://overpass.kumi.systems/api/interpreter"
DATA_DIR = Path(__file__).parent.parent / "data"


def search_local_csv(query_str, cur_lat=None, cur_lng=None):
    """ローカルのすべての道の駅CSVファイルから場所を検索します。"""
    if not DATA_DIR.exists() or not DATA_DIR.is_dir():
        return None
        
    norm_query = query_str.replace("道の駅", "").strip().lower()
    candidates = []
    
    csv_paths = list(DATA_DIR.glob("*.csv"))
    # rawデータは除外
    csv_paths = [p for p in csv_paths if not p.name.endswith("_raw.csv")]
    
    for csv_path in csv_paths:
        try:
            with open(csv_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    name = row.get("name", "")
                    lat_str = row.get("latitude", "")
                    lng_str = row.get("longitude", "")
                    
                    if not name or not lat_str or not lng_str:
                        continue
                        
                    # 部分一致判定
                    if norm_query in name.lower() or name.lower() in norm_query:
                        try:
                            lat = float(lat_str)
                            lng = float(lng_str)
                            candidates.append((lat, lng, f"道の駅 {name}"))
                        except ValueError:
                            continue
        except Exception as e:
            print(f"Warning: CSVファイルの読み込み中にエラーが発生しました ({csv_path.name}): {e}")
        
    if not candidates:
        return None
        
    # 現在地が取得できている場合は、最も近い候補を選ぶ
    if cur_lat is not None and cur_lng is not None:
        candidates.sort(key=lambda x: ((x[0] - cur_lat) ** 2 + (x[1] - cur_lng) ** 2))
        
    return candidates[0]


def search_location(query_str, cur_lat=None, cur_lng=None):
    """ローカルCSVを優先して検索し、なければOverpass APIをフォールバックとして使用します。"""
    # 1. まずローカルの道の駅CSVから検索（高速かつオフラインで動作）
    local_result = search_local_csv(query_str, cur_lat, cur_lng)
    if local_result:
        return local_result
        
    # 2. なければOverpass API (kumi.systems ミラー) で検索
    print(f"ローカルデータに見つからないため、オンライン検索を試みます...")
    overpass_query = '[out:json][timeout:20];(nwr["highway"="rest_area"](38.8,139.5,40.8,142.8););out center;'
    
    quoted_query = urllib.parse.quote(overpass_query, safe='')
    url = f"{OVERPASS_URL}?data={quoted_query}"
    
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", "curl/7.81.0")
    req.add_header("Accept", "*/*")
    
    with urllib.request.urlopen(req, timeout=25.0) as res:
        data = json.loads(res.read().decode("utf-8"))
        
    elements = data.get("elements", [])
    if not elements:
        return None
        
    norm_query = query_str.replace("道の駅", "").strip().lower()
    candidates = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("brand") or tags.get("operator") or ""
        
        if norm_query in name.lower() or name.lower() in norm_query:
            lat = el.get("lat") or el.get("center", {}).get("lat")
            lng = el.get("lon") or el.get("center", {}).get("lon")
            if lat is not None and lng is not None:
                candidates.append((lat, lng, name))
                
    if not candidates:
        return None
        
    if cur_lat is not None and cur_lng is not None:
        candidates.sort(key=lambda x: ((x[0] - cur_lat) ** 2 + (x[1] - cur_lng) ** 2))
        
    return candidates[0]  # (lat, lng, name)


def main(argv=None):
    """CLIエントリポイント。"""
    parser = argparse.ArgumentParser(description="名前で場所を検索し、ARGOSダッシュボードにプロットします。")
    parser.add_argument("-q", "--query", action="append", required=True,
                        help="検索する場所の名前（複数指定可能、例：-q '道の駅 にしね' -q '道の駅 三田貝分校'）")
    parser.add_argument("--target-slot", choices=["center", "right"], default="center",
                        help="対象スロット（center または right、デフォルトは center）")
    parser.add_argument("--color", default="orange", help="マーカーの色（デフォルト：orange）")
    parser.add_argument("--zoom", type=int, default=13, help="ズームレベル（デフォルト：13）")
    parser.add_argument("--current-location", action="store_true", help="現在地も一緒に表示する")
    parser.add_argument("--route", action="store_true", help="目的地間（および現在地）の道路沿いのルートを描画する")
    parser.add_argument("--dry-run", action="store_true", help="送信せずにプロット用の引数と検索結果を表示する")
    
    args = parser.parse_args(argv)
    
    # 必要なら現在地を取得
    cur_lat, cur_lng = None, None
    if args.current_location:
        env_vars = send_overlay.load_env_vars()
        dashboard_host = os.environ.get("ARGOS_DASHBOARD_HOST") or env_vars.get("ARGOS_DASHBOARD_HOST") or "127.0.0.1"
        dashboard_port = os.environ.get("ARGOS_DASHBOARD_PORT") or env_vars.get("ARGOS_DASHBOARD_PORT") or "8765"
        try:
            cur_lat, cur_lng = send_overlay.fetch_current_location(dashboard_host, dashboard_port)
        except Exception as e:
            print(f"Warning: 現在地の取得に失敗しました: {e}")
            
    # 各クエリに対して検索を実行
    points = []
    found_names = []
    
    for q in args.query:
        print(f"検索中: '{q}' ...")
        try:
            result = search_location(q, cur_lat, cur_lng)
        except Exception as e:
            print(f"Error: 検索中にエラーが発生しました ({q}): {e}")
            return 1
            
        if not result:
            print(f"Error: '{q}' が見つかりませんでした。")
            return 1
            
        lat, lng, name = result
        print(f"見つかりました: {name} (緯度: {lat}, 経度: {lng})")
        points.append(f"{lat},{lng},{name},{args.color}")
        found_names.append(name)
        
    # タイトルの作成（複数あれば「〜ほか」）
    if len(found_names) == 1:
        title = found_names[0]
    else:
        title = f"{found_names[0]} ほか {len(found_names)}箇所"
        
    # send_overlay.py の引数を構築
    send_args = [
        "--type", "map",
        "--target-slot", args.target_slot,
        "--title", title,
        "--zoom", str(args.zoom),
        "--label-mode", "popup",
    ]
    
    # 全目的地をプロット
    for p in points:
        send_args.extend(["--point", p])
        
    # 現在地を表示する場合
    if args.current_location:
        send_args.append("--current-location")
        
    # ルートを描画する場合
    if args.route:
        send_args.append("--route")
        
    if args.dry_run:
        send_args.append("--dry-run")
        print(f"send_overlay 実行引数: {send_args}")
        
    return send_overlay.main(send_args)


if __name__ == "__main__":
    sys.exit(main())
