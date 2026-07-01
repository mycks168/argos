# ttyexec

`codex` / `agy` のようなTUIアプリをtmux経由で自動操作し、利用状況(usage)をJSONで取得するスクリプト集。

## 使い方

### codexの使用状況取得

```sh
./codex_status.py
```

`codex`を起動して`/status`を実行し、結果を以下の形式のJSONで標準出力に出力します。

```json
{
  "five_hour": {"usage_pct": 1, "reset": "06/15 19:26"},
  "weekly": {"usage_pct": 100, "reset": "06/18 20:24"},
  "credits": 880
}
```

- `usage_pct`: 使用率(%)
- `reset`: リセット日時(`MM/DD HH:MM`形式)
- `credits`: 残りクレジット数

### agyの使用状況取得

```sh
./agy_usage.py
```

`agy`を起動して`/usage`を実行し、モデルグループ(`gemini` / `claude_gpt`)ごとのWeekly/Five Hour使用率を以下の形式のJSONで標準出力に出力します。
初回起動時にワークスペースの信頼確認や権限確認が表示された場合は、Enterで確認してから`/usage`を実行します。

```json
{
  "gemini": {
    "weekly": {"usage_pct": 61.59, "reset": "06/19 07:00"},
    "five_hour": {"usage_pct": 0.15, "reset": "06/15 22:31"}
  },
  "claude_gpt": {
    "weekly": {"usage_pct": 0.0, "reset": null},
    "five_hour": {"usage_pct": 0.0, "reset": null}
  }
}
```

- `usage_pct`: 使用率(%)。クォータが100%残っている場合は`0.0`
- `reset`: リセット予定日時(`MM/DD HH:MM`形式)。クォータが満タンの場合は`null`

## 動作の仕組み

両スクリプトともtmuxの一時セッション上で対象のCLIを起動し、画面表示が安定するまで待ってからコマンドを送信、`capture-pane`で画面内容を取得して正規表現で解析します。終了時はESC → `/exit`で正常終了させ、残っていればセッションをkillします。

なお`codex`は`/status`を短時間に連続実行すると「refresh requested; run /status again shortly」と表示され値が返らないことがあるため、最大5回まで自動リトライします。

## テスト

```sh
uv run pytest
```

実機のtmux/codex/agyには依存せず、キャプチャ済みの画面出力(`tests/fixtures/`)を使ったパース処理の単体テストのみを実行します。
