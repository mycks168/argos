---
name: waydroid-google-maps
description: Waydroid上で動作するGoogleマップの操作（ハンズフリーナビ開始、ルート全体表示、拡大・縮小、現在地GPS追従制御）を行う。ドライバーが「ナビ開始して」「ルート全体見せて」「地図拡大して」「広域にして」「追従ストップして」「追従再開して」などと発話したときに必ず使用する。
---

# Googleマップ車載制御 (waydroid-google-maps)

Raspberry Pi 5上のWaydroidコンテナ内で動作するGoogleマップを、音声指示で安全・確実に操作するためのスキルです。
車載超横長タッチディスプレイでの「ピンチ操作（拡大縮小）ができない」「画面端のナビ開始ボタンが押せない」「現在地GPS追従により全体表示が勝手にズームされてしまう」といった課題を完全ハンズフリーで解決します。

## 前提と自動で行う処理

- Waydroid（Googleマップ入り）と、GPS中継のユーザーサービス `waydroid-gps-bridge.service` がある端末専用です。Intentの発行に `sudo lxc-attach`（パスワードなしのsudo）を使います。
- Waydroidは既定でコンテナを凍結するため、凍結中はIntentが応答しません。どのコマンドも、実行前に凍結を検知して自動で解除します（`waydroid app launch` を使うのでsudo不要）。
- 画面の左右入れ替えや全画面化は、このスキルの対象外です（`argos.tools.window_layout`。詳細は `docs/waydroid.md`）。

## コマンド一覧

すべての操作は付属のCLIリモコンスクリプト（`scripts/map_control.py`）を介して実行します。実行結果はJSON形式（ユーザーへの読み上げ用 `speech` テキスト付き）で出力されます。

### 1. ナビゲーションの開始（ハンズフリー起動）
画面の「開始」ボタンをタップすることなく、ダイレクトにターンバイターンナビを開始します。GPS追従サービス（`waydroid-gps-bridge`）も自動で再開されます。
```bash
python /opt/argos/skills/waydroid-google-maps/scripts/map_control.py navigate "<目的地>"
```
- **例**: `python /opt/argos/skills/waydroid-google-maps/scripts/map_control.py navigate "東久保養鶏場直売店"`
- **発話例**: 「東久保養鶏場直売店へのナビゲーションを開始するね。現在地を追従中だよ。」

### 2. ルート全体・広域の表示（自動追従ストップ）
Googleマップが現在地に引き戻して勝手に拡大しないよう、GPS追従を自動で一時停止し、ルート全体または広域地図を表示します。
```bash
# 目的地までのルート全景を表示
python /opt/argos/skills/waydroid-google-maps/scripts/map_control.py overview "<目的地>"

# 現在地周辺の広域地図を表示（目的地指定なし）
python /opt/argos/skills/waydroid-google-maps/scripts/map_control.py overview
```
- **発話例**: 「現在地の追従を一時停止して、ルート全体を表示したよ。」

### 3. 地図の拡大（ズームイン）
ピンチイン操作の代わりに、地図を詳細表示（ズームレベル16）へ拡大します。
```bash
python /opt/argos/skills/waydroid-google-maps/scripts/map_control.py zoom-in
```
- **発話例**: 「地図を拡大したよ。」

### 4. 地図の縮小・広域化（ズームアウト）
ピンチアウト操作の代わりに、地図を周辺広域表示（ズームレベル11）へ縮小します。
```bash
python /opt/argos/skills/waydroid-google-maps/scripts/map_control.py zoom-out
```
- **発話例**: 「地図を広域にしたよ。」

### 5. 任意の縮尺変更
ズームレベル（1:世界全体 〜 21:詳細建物）を直接数値で指定します。
```bash
python /opt/argos/skills/waydroid-google-maps/scripts/map_control.py zoom <レベル>
```
- **例**: `python /opt/argos/skills/waydroid-google-maps/scripts/map_control.py zoom 14`

### 6. GPS追従の手動一時停止・再開
手動でGPS追従のみを停止または再開したい場合に使用します。
```bash
# 追従一時停止（地図を自由に見たいとき）
python /opt/argos/skills/waydroid-google-maps/scripts/map_control.py pause-tracking

# 追従再開（元の現在地追従に戻したいとき）
python /opt/argos/skills/waydroid-google-maps/scripts/map_control.py resume-tracking

# 状態確認
python /opt/argos/skills/waydroid-google-maps/scripts/map_control.py status
```

## エージェントの音声応答ルール
- 本スクリプトの実行結果に含まれる `"speech"` フィールドのテキストを参考に、**簡潔かつフレンドリーな日本語**でドライバーへ伝えてください。
- 特に全体表示や広域表示にした際は、**「追従を一時停止した」旨を必ず声で伝える**ことで、ドライバーが現在の追従状態を把握できるように配慮してください。
