# ARGOS agent-limit

ARGOSのダッシュボードに表示するLLM利用枠JSONを生成する同梱ツールです。`codex` / `agy` / `claude` のようなTUIアプリをtmux経由で自動操作し、取得結果をARGOSが読むJSONへ書き出します。

## 使い方

通常は `/opt/argos/services/agent-limit` に配置され、インストーラがARGOS実行ユーザーのcronへ5分おきの更新ジョブを登録します。手動で更新する場合は次を実行します。

```sh
cd /opt/argos/services/agent-limit
uv run ./update_limits.py
```

生成先:

- `codex.json`: Codexの5時間枠、週次枠、credits
- `hermes.json`: Codexと同じ値をHermes枠として表示
- `antigravity.json`: `agy` のGeminiモデルグループ
- `claude.json`: `claude` の現在セッション枠と週次枠

各JSONはダッシュボードの `ARGOS_AGENT_USAGE_COMMAND_<PROVIDER>` から参照されます。

## 個別取得

### codexの使用状況取得

```sh
uv run ./codex_status.py
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

> [!NOTE]
> 最新のCodexなど5時間制限（5h limit）が存在しないモデルの場合、`five_hour` はダミーデータとして `{"usage_pct": 0, "reset": "N/A"}` が返されます。

### agyの使用状況取得

```sh
uv run ./agy_usage.py
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

### claudeの使用状況取得

```sh
uv run ./claude_usage.py
```

`claude`を起動して`/usage`を実行し、現在セッションと週次の使用率を以下の形式のJSONで標準出力に出力します。

```json
{
  "five_hour": {"usage_pct": 12.34, "reset": "06/15 14:45"},
  "weekly": {"usage_pct": 56.78, "reset": "06/18 23:14"}
}
```

## 動作の仕組み

各スクリプトはtmuxの一時セッション上で対象のCLIを起動し、画面表示が安定するまで待ってからコマンドを送信、`capture-pane`で画面内容を取得して正規表現で解析します。終了時はESC → `/exit`で正常終了させ、残っていればセッションをkillします。

`update_limits.py` は `codex_status.py` の結果を `codex.json` と `hermes.json` に、`agy_usage.py` の `gemini` を `antigravity.json` に、`claude_usage.py` の結果を `claude.json` に書き出します。

なお`codex`は`/status`を短時間に連続実行すると「refresh requested; run /status again shortly」と表示され値が返らないことがあるため、最大5回まで自動リトライします。

## テスト

```sh
uv run pytest -c pyproject.toml
```

実機のtmux/codex/agy/claudeには依存せず、キャプチャ済みの画面出力やサンプル文字列を使ったパース処理の単体テストを実行します。
