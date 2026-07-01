"""
Overpass API (OpenStreetMap) を用いて、指定座標周辺の店舗（コンビニ、ガソリンスタンドなど）を検索するスクリプト。
Pythonの標準ライブラリのみで動作し、追加パッケージのインストールは不要です。
"""

import argparse
import json
import urllib.request
import urllib.parse
import urllib.error

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def search_places(lat: float, lon: float, radius: float = 5000.0, facility_type: str = "convenience") -> list:
    """
    指定座標の周辺にある店舗・施設を Overpass API を用いて検索します。

    Args:
        lat: 基準地点の緯度
        lon: 基準地点の経度
        radius: 検索範囲（メートル）
        facility_type: 検索対象の種別 ('convenience': コンビニ, 'fuel': ガソリンスタンド, 'restaurant': レストラン, 'cafe': カフェ)

    Returns:
        list: 検索結果のリスト。距離が近い順にソートされています。
    """
    # 施設種別に応じたクエリフィルタの設定
    if facility_type == "convenience":
        type_filter = 'nwr["shop"="convenience"]'
    elif facility_type == "fuel":
        type_filter = 'nwr["amenity"="fuel"]'
    elif facility_type == "restaurant":
        type_filter = 'nwr["amenity"="restaurant"]'
    elif facility_type == "cafe":
        type_filter = 'nwr["amenity"="cafe"]'
    else:
        type_filter = 'nwr["shop"="convenience"]'

    # Overpass QL クエリの構築 (out center で way/relation の中心座標を取得)
    query = f"""
    [out:json][timeout:25];
    (
      {type_filter}(around:{radius},{lat},{lon});
    );
    out center;
    """

    try:
        # urlencodeの代わりにquoteを使って %20 エンコーディングを強制する
        quoted_query = urllib.parse.quote(query)
        url_with_params = f"{OVERPASS_URL}?data={quoted_query}"
        
        req = urllib.request.Request(url_with_params, method="GET")
        req.add_header("User-Agent", "curl/7.81.0")
        req.add_header("Accept", "*/*")

        # APIリクエストの送信 (タイムアウト30秒)
        with urllib.request.urlopen(req, timeout=30.0) as response:
            res_data = response.read().decode("utf-8")
            data = json.loads(res_data)

        elements = data.get("elements", [])
        results = []

        for el in elements:
            tags = el.get("tags", {})
            
            # 店舗名（nameタグ）の取得。なければブランド名（brand）等で代替
            name = tags.get("name")
            if not name:
                name = tags.get("brand", tags.get("operator", "不明な店舗"))

            # nodeの場合は直接lat/lonがあり、way/relationの場合はcenterオブジェクト内にある
            el_lat = el.get("lat") or el.get("center", {}).get("lat")
            el_lon = el.get("lon") or el.get("center", {}).get("lon")

            if el_lat is not None and el_lon is not None:
                # 緯度経度から簡易的な直線距離（メートル）を算出
                dist = ((el_lat - lat) ** 2 + (el_lon - lon) ** 2) ** 0.5 * 111000.0
                results.append({
                    "name": name,
                    "lat": el_lat,
                    "lon": el_lon,
                    "distance_m": round(dist, 1),
                    "type": facility_type,
                    "brand": tags.get("brand", "")
                })

        # 距離順でソート
        results.sort(key=lambda x: x["distance_m"])
        return results

    except Exception as e:
        # エラーハンドリング
        print(f"Error: Overpass API request failed: {str(e)}")
        return []


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="周辺店舗を検索します。")
    parser.add_argument("--lat", type=float, required=True, help="中心の緯度")
    parser.add_argument("--lon", type=float, required=True, help="中心の経度")
    parser.add_argument("--radius", type=float, default=5000.0, help="検索半径 (m)")
    parser.add_argument(
        "--type", 
        type=str, 
        choices=["convenience", "fuel", "restaurant", "cafe"], 
        default="convenience", 
        help="検索対象のタイプ"
    )

    args = parser.parse_args()

    # 検索を実行し、JSON形式で標準出力へ出力
    places = search_places(args.lat, args.lon, args.radius, args.type)
    print(json.dumps({"results": places}, ensure_ascii=False, indent=2))
