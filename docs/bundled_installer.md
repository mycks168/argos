# ARGOS一式インストーラ設計メモ

## 目的

ARGOS本体だけでなく、周辺サービスとスキルを含めて、1つのリポジトリからセットアップできるようにする。

最終的には、利用者がARGOSリポジトリを取得し、1コマンドで必要なPython仮想環境、`.env`、systemd unit、ダッシュボードkioskを準備できる状態を目指す。

## 現環境で確認したサービス

2026-06-27時点の実機では、次のサービスがARGOSの周辺として動いている。

| サービス | 現在の場所 | 起動形態 | 用途 |
| --- | --- | --- | --- |
| `argos` | `/opt/argos` | systemd system | 音声エージェント本体 |
| `argos-agent-runner` | `/opt/argos` | systemd system | Codex/Antigravityなどの実行分離 |
| `argos-dashboard-kiosk` | `/opt/argos` | systemd user | HDMIダッシュボード表示 |
| `tts-filter` | `/opt/argos/services/tts-filter` | systemd system | 読み上げ前の辞書変換 |
| `argos-acknowledgement-api` | `/opt/argos/services/argos-acknowledgement-api` | systemd system | 相槌、状態通知文言の選択 |
| `argos-reminder` | `/opt/argos/services/argos-reminder` | systemd user | 時刻、位置条件の通知 |
| `gps-server` | `/opt/argos/services/gps-server` | systemd system | GPS位置情報API |
| `screen-recorder` | `/opt/argos/services/screen-recorder` | systemd user | ダッシュボード録画API |
| `agent-limit` | `/opt/argos/services/agent-limit` | ファイル参照 | LLM利用枠JSON生成 |
| `skills` | `/opt/argos/skills` | Codex skill | Slack通知、地図表示、タスク記録など |

外部依存として、現環境では次のサービスも参照している。

| サービス | URL | 扱い |
| --- | --- | --- |
| `stt-gateway` | 環境ごとに設定 | 外部サービスとしてURL設定だけ行う |
| `VOICEVOX Engine` | `http://localhost:50021` | 利用者環境に別途用意する |
| `OSRM` | 環境ごとに設定 | 地図や経路系スキルの外部依存 |

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

別PCをARGOS専用機として初期化する場合は `--bootstrap` も付ける。これにより、`argos` ユーザー作成、`audio` などのデバイスアクセスグループ付与、`alsa-utils` などのOSパッケージ導入、user service用のlinger設定、`/opt/argos` の所有者調整をまとめて行う。
OSパッケージはUbuntuとRaspberry Pi OSの両方を想定し、Chromiumのようにパッケージ名が異なるものは導入可能な候補を自動選択する。

```bash
sudo git clone -b feature/bundled-installer https://github.com/mycks168/argos.git /opt/argos
cd /opt/argos
sudo env "PATH=$PATH" uv run argos-install --bootstrap --configure --apply
```

`--bootstrap` は `argos` ユーザーがなければ作成し、`/opt/argos` の所有者も最終的に `argos:argos` に揃える。ARGOS本体、Agent Runner、TTSフィルター、相槌APIなどは `User=argos` のsystem serviceとして動かす。ダッシュボードkioskとリマインダーは `argos` ユーザーのuser serviceとして動かす。system serviceにも `HOME=/home/argos` と `PATH=/home/argos/.local/bin:/home/argos/.cargo/bin:...` を設定し、Codex、Antigravity、Claude、Hermesの認証情報とCLIを同じユーザー空間に集約する。

`.env.example` は特定ホスト名や特定USBデバイス名を持たない汎用値にする。`--configure` を付けると、STTゲートウェイ、VOICEVOX、VOICEVOX Bearerトークン、OSRM、GPS API、ウェイクワード、Agent Runner、PTT GPIO、入力マイク、出力デバイスを対話式に設定する。GPIOがないUbuntu環境では `ARGOS_PTT_GPIO` を空欄にする。音声デバイスは `arecord -L` と `aplay -L` から候補を表示し、番号選択または直接入力を受け付ける。

インストール済み環境を更新する場合は `--update` を使う。`argos` ユーザーで `git pull --ff-only` を実行し、既存の `.env` は保持したまま `uv sync`、systemd unit再生成、daemon-reload、既定サービス再起動を行う。

```bash
cd /opt/argos
sudo env "PATH=$PATH" uv run argos-install --update
```

OAuth認証はインストーラーで自動化しない。ブラウザ連携や対話操作が必要なため、インストール後に次のように `argos` ユーザーで実行して認証する。

```bash
sudo -iu argos
codex
agy
claude
hermes
```

外部依存として残すSTTゲートウェイ、VOICEVOX、OSRM、Slack Webhookなどは `/opt/argos/.env` で指定する。

`agent-limit` は systemd 常駐サービスではなく補助ツールとして同梱する。インストーラーは `/opt/argos/services/agent-limit/update_limits.py` が存在する場合、ARGOS実行ユーザーのcrontabへ5分おきの更新ジョブを重複なしで登録する。登録済み判定には `# ARGOS agent-limit updater` のマーカーを使う。

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
