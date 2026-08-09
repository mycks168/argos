---
name: assistant-tasklog
description: Use when the user asks to record, queue, backlog, remember, or task-track work items from the conversation, especially ARGOS/Codex follow-up tasks, into the local ~/assistant-tasks repository instead of the active project.
metadata:
  short-description: 会話からタスクをassistant-tasksへ記録する
---

# Assistant Tasklog

会話で出た未着手タスク、後で戻りたい検討事項、ARGOS/Codexへの依頼を `~/assistant-tasks` に記録する。

## 保存先

- ARGOS関連: `~/assistant-tasks/tasks/argos.md`
- 横断タスク: `~/assistant-tasks/tasks/general.md`
- 会話メモ: `~/assistant-tasks/logs/YYYY-MM-DD.md`

`~/assistant-tasks` がなければ、ユーザに確認してから作成する。

## 手順

1. ユーザの発話からタスク名、背景、期待する挙動を抽出する。
2. 既存タスクと重複しないか `rg` で確認する。
3. 該当する `tasks/*.md` の `## 未着手` に追記する。
4. 必要なら当日の `logs/YYYY-MM-DD.md` に経緯を短く追記する。
5. 勝手にコミットしない。ユーザが明示した場合だけコミットする。

## 書き方

タスクは次の形式で書く。

```markdown
### タスク名

背景:

- 何が起きたか
- なぜ後で対応したいか

期待する挙動:

- 完了条件
- 確認したいログや画面表示
```

会話ログは詳細に書きすぎず、後で文脈を戻せる程度にする。
