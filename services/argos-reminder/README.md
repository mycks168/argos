# argos-reminder

ARGOSのダッシュボードへ、指定日時に通知を送る小さなリマインダーサービスです。

## 使い方

```bash
uv sync --extra dev
uv run argos-reminder add "2026-06-19 18:30" "旅費申請"
uv run argos-reminder add-location "目的地に到着" --lat 35.0 --lon 139.0
uv run argos-reminder list
uv run argos-reminder-daemon
```

期限に到達すると、ARGOSの `/api/events` に通知イベントを送ります。既定では通知音と読み上げを有効にします。
位置リマインダーはARGOSの `/api/location` を読み、現在地が指定地点の半径内に入ったら通知します。GPSが取れない場合は何もせず、次回の確認まで待ちます。既定の到着判定半径は100mで、`--radius-m` で変更できます。

## 設定

```text
ARGOS_REMINDER_STATE_PATH=~/.local/state/argos-reminder/reminders.json
ARGOS_DASHBOARD_URL=http://127.0.0.1:8765
ARGOS_DASHBOARD_TOKEN=
ARGOS_REMINDER_POLL_SECONDS=10
```

`ARGOS_DASHBOARD_TOKEN` はARGOS本体の `ARGOS_DASHBOARD_TOKEN` と同じ値を指定します。

## systemd userサービス

常駐起動する場合は、`.env` を作成してからユーザーsystemdへ登録します。

```bash
mkdir -p ~/.config/systemd/user
cp systemd/argos-reminder.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now argos-reminder.service
```

状態確認は次のコマンドで行います。

```bash
systemctl --user status argos-reminder.service
journalctl --user -u argos-reminder.service -f
```
