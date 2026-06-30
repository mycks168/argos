# ARGOS一式インストーラ設計メモ

## 目的

ARGOS本体だけでなく、周辺サービスとスキルを含めて、1つのリポジトリからセットアップできるようにする。

最終的には、利用者がARGOSリポジトリを取得し、1コマンドで必要なPython仮想環境、`.env`、systemd unit、ダッシュボードkioskを準備できる状態を目指す。

## 現環境で確認したサービス

2026-06-27時点の実機では、次のサービスがARGOSの周辺として動いている。

| サービス | 現在の場所 | 起動形態 | 用途 |
| --- | --- | --- | --- |
| `argos` | `/home/yuki/argos` | systemd system | 音声エージェント本体 |
| `argos-agent-runner` | `/home/yuki/argos` | systemd system | Codex/Antigravityなどの実行分離 |
| `argos-dashboard-kiosk` | `/home/yuki/argos` | systemd user | HDMIダッシュボード表示 |
| `tts-filter` | `/home/yuki/tts-filter` | systemd system | 読み上げ前の辞書変換 |
| `argos-acknowledgement-api` | `/home/yuki/argos-acknowledgement-api` | systemd system | 相槌、状態通知文言の選択 |
| `argos-reminder` | `/home/yuki/argos-reminder` | systemd user | 時刻、位置条件の通知 |
| `gps-server` | `/home/yuki/car-logger/raspberry` | systemd system | GPS位置情報API |
| `screen-recorder` | `/home/yuki/screen-recorder` | systemd user | ダッシュボード録画API |
| `agent-limit` | `/home/yuki/agent-limit` | ファイル参照 | LLM利用枠JSON生成 |
| `skills` | `/home/yuki/skills` | Codex skill | Slack通知、地図表示、タスク記録など |

外部依存として、現環境では次のサービスも参照している。

| サービス | URL | 扱い |
| --- | --- | --- |
| `stt-gateway` | `http://clove:23000` | 外部サービスとしてURL設定だけ行う |
| `VOICEVOX Engine` | `http://localhost:50021` | 利用者環境に別途用意する |
| `OSRM` | `http://clove:5001` | 地図や経路系スキルの外部依存 |

## 取り込み方針

ARGOS本体に `installer/services.json` を置き、どのサービスを一式に含めるかを機械可読にする。

サービスコードの取り込み状態は次の分類で管理する。

1. `core`: ARGOS本体に既に含まれているもの。
2. `bundled`: `services/` または `skills/` 配下へ同梱済みのもの。
3. `optional`: 車載構成や開発補助で使うもの。既定では有効化しない。
4. `external`: GPUサーバーやVOICEVOXなど、ARGOSリポジトリ内へ入れないもの。

サブモジュールは使わない。利用者が追加操作を忘れやすいため、取り込む場合は履歴なしコピーまたは必要なら `git subtree` を検討する。

## 予定ディレクトリ構成

```text
argos/
  src/argos/                 # ARGOS本体
  services/
    tts-filter/
    argos-acknowledgement-api/
    argos-reminder/
    agent-limit/
    gps-server/
    screen-recorder/
  skills/
    slack-notifier/
    dashboard-overlay/
    argos-reminder/
    ...
  installer/
    services.json
  systemd/
    *.service
  docs/
    bundled_installer.md
```

## インストーラ骨格

`uv run argos-install` は、既定ではdry-runでインストール計画を表示する。

```bash
uv run argos-install
uv run argos-install --json
```

実際に仮想環境作成、`.env` 作成、systemd unit生成、enable/startまで行う場合は `--apply` を付ける。

```bash
uv run argos-install --apply
```

ウェイクワードを標準機能として扱うため、ARGOS本体の通常依存に `onnxruntime` と `numpy` を含める。これにより、`--extra wakeword` を指定しなくてもONNXモデルの実行に必要なランタイムが入る。

ウェイクワードの実行に必要なONNXモデルは `models/wakeword/` に同梱する。

- `argos.onnx`: 「アルゴス」検知用の分類器
- `melspectrogram.onnx`: 音声からメル特徴量を作る前処理モデル
- `embedding_model.onnx`: 音声埋め込みモデル
- `silero_vad_v6.onnx`: ウェイクワード後の発話終了判定に使うVADモデル

`ARGOS_WAKEWORD_MODEL_DIR` は既定で `models/wakeword` を参照するため、リポジトリを `/opt/argos` に配置して `uv run argos-install --apply` した場合は追加コピーなしで利用できる。別パスへモデルを置く場合だけ `.env` で `ARGOS_WAKEWORD_MODEL_DIR` または `ARGOS_WAKEWORD_VAD_MODEL` を上書きする。

unit生成だけ確認したい場合は、出力先を一時ディレクトリへ向けて `--no-enable` を付ける。

```bash
uv run argos-install --apply --no-enable \
  --system-unit-dir /tmp/argos-systemd \
  --user-unit-dir /tmp/argos-user-systemd
```

## 次の実装単位

1. `gps-server` と `screen-recorder` を任意同梱サービスとして取り込むか判断する。
2. `stt-gateway` を外部サービスのままにするか、GPUサーバー向け別インストーラを作るか決める。
3. `VOICEVOX Engine` の導入手順を、Dockerまたは既存バイナリ前提で整理する。
4. `argos-install --apply` の実機リハーサルを行い、systemd権限やユーザーBusまわりを調整する。

## 注意点

- `tts-filter` は `feature/tmux-ttyd-dictionary` ブランチの内容を取り込んだ。
- `argos-acknowledgement-api` は未コミット変更を含む現在の実体を取り込んだ。
- `screen-recorder` と `argos-agent-scheduler` はまだ初回コミットがない。
- `stt-gateway` とOSRMは現環境ではGPUサーバー側にあり、ARGOS本体へ含めるより外部依存として扱う方が現実的。
