---
name: place-search
description: 現在地の緯度経度から指定の半径内にある周辺店舗（コンビニ、ガソリンスタンド、飲食店、カフェ）を完全無料で正確に検索する。ユーザーが「近くのコンビニを探して」「周辺のガソリンスタンドは？」「近くにカフェある？」などと尋ねたときに使用する。
---

# 周辺店舗検索 (place-search)

指定した緯度経度（現在地）の周辺にあるコンビニやガソリンスタンドなどの施設情報を、完全に無料の `Overpass API` (OpenStreetMap) を用いて正確に検索します。
Web検索の代わりに本スキルが提供するスクリプトを実行することで、位置情報や店舗名の検索ミスを防ぎます。

## 基本コマンド

### 1. コンビニを探す
現在地周辺のコンビニを最大5kmの範囲で検索します。
```bash
python ~/skills/place-search/scripts/search_places.py \
  --lat 40.590215 \
  --lon 140.327849 \
  --radius 5000 \
  --type convenience
```

### 2. ガソリンスタンドを探す
現在地周辺のガソリンスタンドを検索します。
```bash
python ~/skills/place-search/scripts/search_places.py \
  --lat 40.590215 \
  --lon 140.327849 \
  --radius 10000 \
  --type fuel
```

### 3. 飲食店・カフェを探す
```bash
python ~/skills/place-search/scripts/search_places.py \
  --lat 40.590215 \
  --lon 140.327849 \
  --type restaurant
```

## 注意事項
- 緯度経度の入力は、常に最新のGPS取得結果を使用してください。
- 検索された結果を地図へ反映させたい場合は、 `dashboard-overlay` スキルを併用して `--point` にて座標をプロットしてください。
- 検索半径はデフォルトで5000メートル (5km) です。状況に応じて最大で20000メートル (20km) 程度まで広げてクエリしてください。
- **通信仕様**: Overpass API サーバーによる拒否（406 Not Acceptable）を避けるため、スクリプトは `GET` メソッドを使用し、User-Agent ヘッダーを `curl/7.81.0` に設定してリクエストを送信します。また、スペースのURLエンコードに `%20` を強制するよう設計されています。
