# 基本設計

## 外部仕様

### CLI

`argos-reminder add <日時> <タイトル>` でリマインダーを登録します。
`argos-reminder add-location <タイトル> --lat <緯度> --lon <経度>` で位置リマインダーを登録します。
位置リマインダーの到着判定半径は既定で100mです。`--radius-m` で変更できます。

日時は次の形式を受け付けます。

- `YYYY-MM-DD HH:MM`
- `YYYY-MM-DDTHH:MM`
- `YYYY-MM-DDTHH:MM:SS`

`argos-reminder list` は未送信のリマインダーを一覧表示します。

`argos-reminder remove <id>` は指定IDのリマインダーを削除します。

`argos-reminder run-once` は期限到達済みの未送信リマインダーを1回だけ送信します。

`argos-reminder-daemon` は一定間隔で `run-once` 相当の処理を繰り返します。
未送信の位置リマインダーがある場合はARGOSの `/api/location` を1回だけ読み、全位置リマインダーをまとめて距離判定します。GPSが取れない場合は送信せず、次回ポーリングまで待ちます。

### ARGOS通知

期限到達時はARGOSダッシュボードAPIへ次の通知イベントを送ります。

```json
{
  "type": "notification",
  "title": "旅費申請",
  "text": "",
  "source": "Reminder",
  "priority": "normal",
  "sound": true,
  "speak": true
}
```

ARGOS側で `sound` と `speak` を解釈し、通知音と読み上げを行います。

## 保存形式

`ARGOS_REMINDER_STATE_PATH` のJSONファイルに保存します。

```json
{
  "reminders": [
    {
      "id": "20260619183000-abcdef",
      "kind": "time",
      "scheduled_at": "2026-06-19T18:30:00+09:00",
      "title": "旅費申請",
      "text": "",
      "source": "Reminder",
      "sound": true,
      "speak": true,
      "sent_at": null,
      "created_at": "2026-06-19T12:00:00+09:00",
      "target_lat": null,
      "target_lon": null,
      "radius_m": 100.0
    },
    {
      "id": "loc-20260619120000-abcdef",
      "kind": "location",
      "scheduled_at": null,
      "title": "目的地に到着",
      "text": "",
      "source": "Reminder",
      "sound": true,
      "speak": true,
      "sent_at": null,
      "created_at": "2026-06-19T12:00:00+09:00",
      "target_lat": 35.0,
      "target_lon": 139.0,
      "radius_m": 100.0
    }
  ]
}
```
