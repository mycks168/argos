---
name: timer
description: ARGOSのタイマーを始める・残り時間を答える・止める。複数のタイマーを同時に動かせ、ダッシュボードの右側に残り時間を出し、時間になったら通知音と読み上げで知らせる。「3分タイマー」「パスタ7分で教えて」「タイマーあと何分」「タイマー止めて」などと言われたときに使う。
---

# ARGOSのタイマー

`scripts/argos_timer.py` で操作する。高速振り分けやエージェントからは、`argos-tool` の `timer_start`・`timer_list`・`timer_cancel` でも使える。

```bash
cd /opt/argos
uv run argos-tool run timer_start --args '{"minutes": 3, "label": "パスタ"}'
uv run argos-tool run timer_list
uv run argos-tool run timer_cancel --args '{"label": "パスタ"}'   # label がなければ全部止める
```

- 時間は `hours`・`minutes`・`seconds` を組み合わせる（合計1秒〜24時間）。同時に10個まで。
- 名前がなければ「タイマー1」「タイマー2」…、同じ名前があれば「パスタ2」のように番号を付ける。
- 時間を数えるのは systemd のユーザータイマー（`argos-timer-<id>`）。ARGOSを再起動しても消えない。
- 動いているタイマーは、ダッシュボードの右側（`/static/timers.html`）に終わる順で並ぶ。全部終わる・止めると閉じる。
- 時間になると「〇〇のタイマーが終わったよ。」と通知し、音と読み上げで知らせる（1回）。
- 状態は `~/.local/state/argos-timer/timers.json`。返事の文は標準出力の最後の行に出る。
