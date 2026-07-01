---
name: argos-reminder
description: ARGOSのリマインダーを登録、一覧、削除する。ユーザーが「何時に通知して」「今日の何時にリマインダー」「ここに近づいたら通知」「場所に着いたら知らせて」「到着したら通知」「リマインダー一覧」「リマインダー消して」など、日時または位置に基づく通知を求めたときに使う。
---

# ARGOS Reminder

`/home/yuki/argos-reminder` のCLIでARGOSへ通知するリマインダーを管理する。

## 基本ルール

- コマンドは `/home/yuki/argos-reminder` で `uv run argos-reminder ...` として実行する。
- 実行後のリマインダーIDは、ユーザーに求められた場合だけ伝える。通常は読み上げない。
- `.env` にはBearer tokenが入るため、内容を表示しない。
- `argos-reminder.service` が常駐して期限到達や位置到着を検出する。登録だけならサービス再起動は不要。
- コード変更後だけ `systemctl --user restart argos-reminder.service` を行う。
- ユーザーからリマインダーや通知の登録・追加を依頼された場合は、方針の確認をせずに即座に実行する。

## 日時リマインダー

日時は必ず絶対日時に直して登録する。相対表現は現在日付とAsia/Tokyoで解釈する。

```bash
uv run argos-reminder add "2026-06-20 06:50" "旅費申請をする"
```

本文を追加する場合:

```bash
uv run argos-reminder add "2026-06-20 06:50" "旅費申請をする" --text "忘れずに"
```

## 位置リマインダー

指定地点から既定100m以内に入ったら通知する。GPSが取れない場合は何もせず次回確認を待つ。

```bash
uv run argos-reminder add-location "打刻修正する" --lat 41.254018 --lon 140.346587 --text "道の駅みんまや付近です"
```

半径を変える場合:

```bash
uv run argos-reminder add-location "目的地に到着" --lat 35.0 --lon 139.0 --radius-m 200
```

場所名だけで依頼された場合は、Web検索などで緯度経度を確認してから登録する。曖昧な場所名なら確認する。

## 一覧と削除

```bash
uv run argos-reminder list
uv run argos-reminder remove <id>
```

削除にはIDが必要なので、削除依頼時は一覧から該当IDを使う。複数候補がある場合は確認する。

## 動作確認

サービス状態:

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=${XDG_RUNTIME_DIR}/bus
systemctl --user status argos-reminder.service --no-pager
```

ARGOS通知ログを軽く見る場合:

```bash
journalctl -u argos.service -n 80 --no-pager | rg "api/events|状態通知|リマインダー|通知"
```
