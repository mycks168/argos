---
name: dashboard-overlay
description: ARGOSダッシュボードの中央スロットまたは右スロットに、地図、現在地追従地図、Markdown文書、画像、任意HTML/Webページを表示し、表示スロットの消去や中央/右の入れ替えを行う。ユーザーが「画面に出して」「ダッシュボードに表示して」「地図を出して」「このファイルを表示して」「画像を見せて」「右側に出して」「中央に出して」「表示を消して」「左右を入れ替えて」などと言ったときに使う。
---

# ARGOSダッシュボード表示

ARGOSダッシュボードへ表示イベントを送る。基本は bundled script を使う。

```bash
uv run python ${ARGOS_SKILLS_DIR:-/opt/argos/skills}/dashboard-overlay/scripts/send_overlay.py --type markdown --title "確認" --content "表示テスト"
```

## 地図の最速コマンド

任意の場所をマーカー表示し、現在地も自動取得して一緒に出す。右ペインではラベルが邪魔になりやすいので `--label-mode popup` を既定の使い方にする。

```bash
uv run python ${ARGOS_SKILLS_DIR:-/opt/argos/skills}/dashboard-overlay/scripts/send_overlay.py \
  --type map \
  --target-slot right \
  --title "目的地" \
  --current-location \
  --label-mode popup \
  --point "35.681236,139.767125,東京駅,#ff4d4d"
```

複数地点を出す場合は `--point "緯度,経度,名前,色"` （色は任意。カラーコードやカラー名）を必要なだけ追加する。

## 名前で検索してプロット（自動化）

緯度経度を手動で調べずに、場所の名前（クエリ）を指定してダッシュボードの地図上に直接マーカーをプロットできます。
候補が複数ある場合で `--current-location` が指定されているときは、現在地から一番近い候補を自動で選択します。

```bash
uv run python ${ARGOS_SKILLS_DIR:-/opt/argos/skills}/dashboard-overlay/scripts/search_and_plot.py \
  -q "道の駅 にしね" \
  -q "道の駅 石神の丘" \
  -q "道の駅 三田貝分校" \
  --target-slot right \
  --color orange \
  --current-location
```

- `-q / --query`: 検索するキーワード（部分一致可、複数指定可能）。複数指定すると1つの地図に同時にプロットされます。
- `--color`: マーカーの色（デフォルトは `orange`）。
- `--target-slot`: 表示するスロット（`center` または `right`、デフォルトは `center`）。
- `--current-location`: 現在地も一緒に地図上に表示し、複数候補時の距離判定に使用する。
- `--dry-run`: 実際には送信せず、検索結果と送信されるコマンドライン引数の確認を行う。

## 経路（ルート）の描画

`send_overlay.py` および `search_and_plot.py` に `--route` オプションを指定することで、プロットされた地点（および現在地）を道路沿いに結ぶルートラインを描画します。

```bash
uv run python ${ARGOS_SKILLS_DIR:-/opt/argos/skills}/dashboard-overlay/scripts/search_and_plot.py \
  -q "道の駅 にしね" \
  -q "道の駅 三田貝分校" \
  --target-slot right \
  --current-location \
  --route
```

- `--route`: 目的地間（および現在地）の道路沿いのルートを描画する。
- `--osrm-url`: OSRMサーバーのURL（省略時は環境変数または `.env` の `OSRM_URL` を使用）。

## 共通ルール

- 右側に小さく出す場合は `--target-slot right` を使う。省略時も `right`。
- 中央に大きく出す場合は `--target-slot center` を使う。
- まずpayloadだけ確認したい場合は `--dry-run` を付ける。
- 右ペインの地図でラベルが邪魔な場合は `--label-mode popup` を使う。タップしたマーカーだけ名前を表示する。
- ARGOSの接続先とBearer tokenは環境変数または `ARGOS_ENV_FILE`、カレントディレクトリ、スキル親ディレクトリ、または `/opt/argos/.env` の `ARGOS_DASHBOARD_HOST`、`ARGOS_DASHBOARD_PORT`、`ARGOS_DASHBOARD_TOKEN` から読む。
- ユーザーが「表示して」「出して」など地図やダッシュボードへの表示を求めた場合は、いちいち方針確認をせずに、即座に送信（表示）する。

## Markdownを表示

ファイル内容を中央に表示する。

```bash
uv run python ${ARGOS_SKILLS_DIR:-/opt/argos/skills}/dashboard-overlay/scripts/send_overlay.py \
  --type markdown \
  --target-slot center \
  --title "設計メモ" \
  --file "${ARGOS_HOME:-/opt/argos}/docs/basic_design.md"
```

短いテキストを右側に表示する。

```bash
uv run python ${ARGOS_SKILLS_DIR:-/opt/argos/skills}/dashboard-overlay/scripts/send_overlay.py \
  --type markdown \
  --target-slot right \
  --title "メモ" \
  --content "確認したい内容"
```

## 地図を表示

よく使うルートが分かっている場合だけプリセットも使える。ただし、基本は上の汎用 `--point` 指定を使う。

```bash
uv run python ${ARGOS_SKILLS_DIR:-/opt/argos/skills}/dashboard-overlay/scripts/send_overlay.py \
  --preset tsuruoka-roadstations \
  --current-location
```

現在地を追従する地図を中央に表示する。`/api/location` から現在地を取れる場合は `--cur-lat` と `--cur-lng` を省略してもよい。

```bash
uv run python ${ARGOS_SKILLS_DIR:-/opt/argos/skills}/dashboard-overlay/scripts/send_overlay.py \
  --type map \
  --target-slot center \
  --title "現在地周辺" \
  --zoom 14 \
  --label-mode popup
```

目的地や経由地を複数表示する（色分け表示も可能）。

```bash
uv run python ${ARGOS_SKILLS_DIR:-/opt/argos/skills}/dashboard-overlay/scripts/send_overlay.py \
  --type map \
  --target-slot center \
  --title "候補地" \
  --label-mode popup \
  --point "35.681236,139.767125,東京駅" \
  --point "35.658581,139.745433,東京タワー,#2563eb"
```

単一の目的地を色付きで表示する。

```bash
uv run python ${ARGOS_SKILLS_DIR:-/opt/argos/skills}/dashboard-overlay/scripts/send_overlay.py \
  --type map \
  --target-slot right \
  --title "目的地" \
  --lat 35.681236 --lng 139.767125 --color "#ff9900"
```

現在地追従を止めたい場合は `--no-follow-current` を付ける。

## カーナビ風の現在地追従地図を表示

中央スロットに、現在地を常に中央へ追従するナビ地図を表示する。右スロットの通常地図とは別に同時表示できる。
ナビ地図は最前面を差し替えるため、ズーム変更などで再表示しても「閉じる」を何度も押す必要はない。

```bash
uv run python ${ARGOS_SKILLS_DIR:-/opt/argos/skills}/dashboard-overlay/scripts/send_overlay.py \
  --type nav \
  --target-slot center \
  --title "ナビ" \
  --zoom 15 \
  --orientation north
```

進行方向を上にする場合は `--orientation heading` を指定する。進行方向が取得できない場合は北上表示に近い挙動へフォールバックする。

## 画像を表示

```bash
uv run python ${ARGOS_SKILLS_DIR:-/opt/argos/skills}/dashboard-overlay/scripts/send_overlay.py \
  --type image \
  --target-slot center \
  --title "画像確認" \
  --url "/camera/latest.jpg"
```

## HTML/Webページを表示

```bash
uv run python ${ARGOS_SKILLS_DIR:-/opt/argos/skills}/dashboard-overlay/scripts/send_overlay.py \
  --type html \
  --target-slot right \
  --title "Web表示" \
  --url "https://example.com/"
```

## 表示を消す

右スロットだけ閉じる。

```bash
uv run python ${ARGOS_SKILLS_DIR:-/opt/argos/skills}/dashboard-overlay/scripts/send_overlay.py --type clear --target-slot right
```

中央スロットだけ閉じる。

```bash
uv run python ${ARGOS_SKILLS_DIR:-/opt/argos/skills}/dashboard-overlay/scripts/send_overlay.py --type clear --target-slot center
```

## 中央と右を入れ替える

```bash
uv run python ${ARGOS_SKILLS_DIR:-/opt/argos/skills}/dashboard-overlay/scripts/send_overlay.py --type swap
```

## 注意事項・再確認項目

- **位置情報のダブルチェック**: 地図に表示する地点（特に道の駅や店舗）の緯度・経度は、Web検索等で最新の正しい座標を必ずダブルチェックすること。
- **施設条件の確認**: ユーザーが「セルフ」「24時間営業」などの条件を指定した場合は、候補地の施設がその条件（セルフ給油かフルサービスか等）に本当に合致しているか確認すること。

## 台風情報の表示と定期更新

tenki.jp から最新の台風画像をダウンロードし、ダッシュボードに表示します。表示モードとして日本近海、広域、または個別台風にズームした詳細表示が選択可能です。

```bash
uv run python ${ARGOS_SKILLS_DIR:-/opt/argos/skills}/dashboard-overlay/scripts/update_typhoon_image.py --target-slot center --mode near
```

- `--target-slot`: 表示するスロット（`center` または `right`、デフォルトは `center`）
- `--mode`: 表示モード。`near` (日本近海、デフォルト), `wide` (広域), `detail` (個別台風ズーム) から選択
- `--typhoon-id`: 特定の台風ID（例: `2608`）を指定してズーム表示。省略時は自動で最新の台風を検出
- `--dry-run`: 実際にはダッシュボードに送信せず、ダウンロードのみを実行する

このスクリプトを実行することで、最新の台風画像をダウンロードして `/static/typhoon.jpg` に保存し、ダッシュボードへ画像表示イベントを送信します。

## 台風進路予測マップ（HTMLプロット）の表示

tenki.jp から台風の実況・予報位置（緯度経度）を取得し、日本地図（Wikimedia素材）の上にプロットしたHTML地図を生成してダッシュボードに表示します。

```bash
uv run python ${ARGOS_SKILLS_DIR:-/opt/argos/skills}/dashboard-overlay/scripts/generate_typhoon_html.py --target-slot center
```

- `--target-slot`: 表示するスロット（`center` または `right`、デフォルトは `center`）
- `--typhoon-id`: 対象の台風ID（例: `2608`）。省略時は自動で最新の台風を選択
- `--dry-run`: ダッシュボードに送信せず、HTML生成のみを行う

生成されたHTMLはダッシュボードの静的ディレクトリ（`/static/typhoon_map.html`）に保存され、ダッシュボードに送信されます。


