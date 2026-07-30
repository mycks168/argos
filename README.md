# ARGOS

ARGOS（Autonomous Road Guardian & Observation System）は、**声とブラウザ画面から AI エージェントを操作する常駐アシスタント**です。

話しかけると音声を文字起こしし、Codex CLI・Claude Code・Antigravity CLI・Hermes といったエージェント CLI へ渡し、返ってきた答えを読み上げます。同時に HDMI ディスプレイやスマートフォンのブラウザから、同じ会話をテキストで続けたり、状態を眺めたりできます。

Raspberry Pi へ組み込んで車載端末にもできますが、机の上に置いて常駐アシスタントとして使うことも、LAN 内の別の端末から操作することもできます。

## できること

- **声で操作する** — ウェイクワード「アルゴス」、または PTT スイッチ・キーボード・USB ペダルで話しかける
- **複数のエージェントを使い分ける** — 会話スロットごとに provider・作業ディレクトリ・モデル・音声を分けられる。別ホストの ARGOS もスロットとして扱える
- **画面から使う** — ブラウザで会話履歴、現在のスロット、地図、通知を表示。テキスト入力でも会話できる
- **通知を受け取る** — 外部サービスから HTTP で通知を送ると、画面に出し、必要なら読み上げる
- **本人確認** — 合言葉と顔認証で、許可した人の発話だけをエージェントへ渡す
- **会話を引き継ぐ** — 再起動しても会話履歴を復元し、要約してセッションを作り直せる

詳しい使い方は [利用者マニュアル](docs/user_manual.md) を参照してください。

## 動作環境

- Raspberry Pi OS / Ubuntu などの Linux
- Python 3.11 または 3.12（互換 Python がなければ `uv` が自動で用意します）
- マイクとスピーカー

GPIO・ST7789 LCD・GPS は車載構成で使う任意の機能です。無くても動作します。

## 必要な外部サービス

| サービス | 用途 |
| --- | --- |
| stt-gateway | 音声の文字起こし（`POST /transcribe`） |
| VOICEVOX Engine | 音声合成（`POST /audio_query`、`POST /synthesis`） |
| tts-filter | 読み上げ前のテキスト整形（`POST /normalize`、任意） |

tts-filter・相槌 API・リマインダーはリポジトリに同梱していて、インストーラーが同じホストへ導入します。stt-gateway と VOICEVOX は別途用意して URL を設定します。

エージェント CLI は使うものだけあれば十分です（Codex CLI / Claude Code / Antigravity CLI / Hermes）。

## 事前準備

### uv を入れる

インストーラーを実行する端末に `uv` が必要です。

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
```

### エージェント CLI を入れる

CLI の導入と初回ログインは自動化していません。`--bootstrap` 実行後に、ARGOS 実行ユーザーで済ませてください。

```bash
sudo -iu argos

curl -fsSL https://chatgpt.com/codex/install.sh | sh && codex          # Codex CLI
curl -fsSL https://claude.ai/install.sh | bash && claude               # Claude Code
curl -fsSL https://antigravity.google/cli/install.sh | bash && agy     # Antigravity CLI
```

`command -v codex` のように、ARGOS 実行ユーザーの PATH から見えることを確認します。

公式手順: [uv](https://docs.astral.sh/uv/getting-started/installation/) / [Codex CLI](https://developers.openai.com/codex/cli) / [Claude Code](https://code.claude.com/docs/en/quickstart) / [Antigravity CLI](https://github.com/google-antigravity/antigravity-cli)

## インストール

### 専用機として初期化する

Raspberry Pi や別 PC を ARGOS 専用機にする場合は、リポジトリを `/opt/argos` へ置いて一式インストーラを実行します。`argos` ユーザーの作成、OS パッケージ導入、デバイス権限、systemd ユニット生成、起動までまとめて行います。

```bash
sudo git clone https://github.com/mycks168/argos.git /opt/argos
cd /opt/argos
sudo env "PATH=$PATH" uv run argos-install --bootstrap --configure --apply
```

`--configure` は STT ゲートウェイ、VOICEVOX、マイク、スピーカー、会話スロットなどを対話で `config.yaml` へ書き込みます。

### 既存環境へ入れる

ユーザー作成や OS パッケージ導入が不要な場合は `--bootstrap` を外します。

```bash
uv run argos-install --configure --apply
```

何をするかだけ先に確認する場合は、引数なし（または `--json`）で実行します。

```bash
uv run argos-install
```

### 起動を確認する

```bash
systemctl status argos.service
journalctl -u argos.service -f
```

ブラウザで `http://<ホスト>:8765/` を開くとダッシュボードが表示されます。

## 更新する

Git pull、依存更新、systemd ユニット再生成、サービス再起動までまとめて行います。`config.yaml` は上書きしません。

```bash
cd /opt/argos
sudo env "PATH=$PATH" uv run argos-install --update
```

設定を変えたときは、`config.yaml` を編集してから再起動します。

```bash
sudo systemctl restart argos.service
```

## ドキュメント

| 文書 | 内容 |
| --- | --- |
| [利用者マニュアル](docs/user_manual.md) | 日々の操作方法。声、画面、通知、車載構成 |
| [コンセプト](docs/concept.md) | 何を目指しているか |
| [要件定義](docs/requirements.md) | 要求事項 |
| [基本設計](docs/basic_design.md) | 設定ファイルの全項目と外部仕様 |
| [同梱インストーラ](docs/bundled_installer.md) | 同梱サービスと導入方針 |
