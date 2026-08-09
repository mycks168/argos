---
name: agent-scheduler
description: 任意のプロンプトでAIエージェントに定期確認させ、その結果をARGOSに自動通知・読み上げさせる汎用定期ジョブを登録・管理する。「毎日何時に〜を通知して」「毎週何曜日に〜をチェックして知らせて」「1時間おきに〜を確認して」など、定期的なAI確認タスクの登録・変更・削除を求められたときに使う。
---

# ARGOS 汎用エージェント通知スケジューラ管理 (agent-scheduler)

`~/argos-agent-scheduler` にある汎用スケジューラプログラムを用いて、ユーザーから依頼された定期確認タスクを自動でスケジュール登録・管理します。

## ジョブの登録手順

ユーザーから「毎日何時に〜を通知して」などの定期確認タスクを依頼された場合、以下の手順で自動的にジョブとcronスケジュールを登録します。方針確認は行わず、即座に登録を完了してください。

### 1. プロンプトファイルの作成
`~/argos-agent-scheduler/prompts/<job_name>.txt` ファイルを作成し、AIに調べさせたい内容を記述します。
※回答の最後に必ず `【通知用要約】` または `[要約]` というプレフィックスを付けて、読み上げ用の1行要約（80〜100文字程度）を出力させる指示を含めてください。

**プロンプト例（天気の場合）:**
```markdown
【重要：実行指示】
本処理は自動実行のバッチ処理です。対話的なプロセスは一切行わず、結果のみを出力してください。

【指示】
1. 本日の天気予報についてWeb検索を用いて調べてください。
2. 気温、降水確率、および雨風の強まりなどについて整理してください。
3. 回答の最後に必ず、以下の形式で1行の要約を出力してください。
   【通知用要約】本日の天気の要約と傘の必要性など。
```

### 2. ジョブ設定の追加 (`config.json`)
`~/argos-agent-scheduler/config.json` を読み込み、`jobs` の配下に新しいジョブ定義を追記して上書き保存します。

**設定項目:**
- `title`: 通知されるタイトル。
- `prompt_file`: 手順1で作成したプロンプトファイルの相対パス（例: `prompts/daily_weather.txt`）。
- `session_file`: （推奨）会話履歴を保持するためのファイル名（例: `.session_weather`）。指定すると過去の会話を踏まえた分析ができます。
- `notification_source`: 送信元としてダッシュボードに表示される名前（例: `weather-reporter`）。
- `priority`: 重要度。急ぎや重要なものは `high`、それ以外は `normal` または `low`。

**設定例:**
```json
"daily-weather": {
  "title": "今日の天気予報",
  "prompt_file": "prompts/daily_weather.txt",
  "session_file": ".session_weather",
  "notification_source": "weather-reporter",
  "priority": "normal"
}
```

### 3. cronスケジュールへの登録
現在の `crontab -l` から設定を取得し、指定された時刻にジョブを実行するcronエントリを末尾に追加して `crontab -` で再登録します。

**cronコマンドの標準形式:**
```cron
<分> <時> * * * cd ~/argos-agent-scheduler && ~/.local/bin/uv run python -m agent_scheduler.main --job <job_name> >> ~/argos-agent-scheduler/cron.log 2>&1
```

## ジョブの一覧と削除手順

### ジョブの一覧確認
`~/argos-agent-scheduler/config.json` を表示して登録されているジョブ名の一覧を確認し、`crontab -l` でそれぞれのスケジュールを確認します。

### ジョブの削除
ユーザーから「〜の定期通知をやめて」と依頼された場合：
1. `crontab -l` から該当するジョブ名（`--job <job_name>`）が含まれる行を削除して再登録します。
2. `config.json` の `jobs` から該当するジョブ設定を削除します。
3. 必要に応じて、`prompts/<job_name>.txt` と `.session_<job_name>` を削除します。
