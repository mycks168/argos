# ARGOS

ARGOS（Autonomous Road Guardian & Observation System）は、Raspberry Pi の PTT スイッチで録音し、stt-gateway、Codex CLI、tts-filter、VOICEVOX をつないで音声で Codex を操作するエージェントです。

## 使い方

```bash
cd /home/pi/argos
uv sync --extra dev
cp .env.example .env
uv run argos
```

テキストだけで確認する場合:

```bash
DRY_RUN=true uv run argos
```

DRY_RUN では `/next` で Codex スロット切替、`/reset` で現在スロットを新規会話扱いにします。

## systemd での起動

サービスとして常駐させる場合:

```bash
cd /home/pi/argos
uv sync
sudo cp systemd/argos.service /etc/systemd/system/argos.service
sudo systemctl daemon-reload
sudo systemctl enable --now argos.service
```

状態確認とログ確認:

```bash
systemctl status argos.service
journalctl -u argos.service -f
```

設定を変更した場合は `.env` を更新してから再起動します。

```bash
sudo systemctl restart argos.service
```

## PTT 操作

- PTT ON: 録音開始
- PTT OFF: 録音停止、文字起こし、Codex 実行、読み上げ
- 短押し1回: 録音を破棄
- 短押し2回: Codex スロット切替
- 処理中に短押し: 再生中の音声を止め、録音は破棄
- 処理中に押し続ける: 再生中の音声を止め、そのまま録音開始

処理中の読み上げを止めて次の録音を始めた場合、前の処理の終了タイミングでは録音中状態を維持し、ボタン解放で録音停止と送信へ進みます。

## 読み上げ

Codex の応答は `--json` の JSONL イベントから取得し、句読点や改行で分割して VOICEVOX に順次投入します。キャンセル時は再生中の音声と未再生チャンクを破棄します。

既定の区切り文字:

- `。`
- `！`
- `？`
- `!`
- `?`
- 改行

`.` は `README.md` や `systemd.service` などを途中で分割しないよう、既定では区切り文字に含めません。変更する場合は `.env` の `ARGOS_TTS_DELIMITERS` に区切り文字を並べます。改行は常に区切りとして扱います。

Codex CLI が途中イベントを出した場合は、その差分から順に処理します。CLI 側が最終回答までイベントを出さない場合でも、最終回答は上記の区切りで分割して読み上げます。

Codex を呼び出した直後は、作業を始めたことを短い音声で通知します。応答が遅い場合は、待機中であることを一定間隔で追加通知します。通知文はAI名を出さず、「今やってるから、少し待ってね」のように音声で聞きやすい親しみのある言い方を複数候補からランダムに選びます。待機通知の再生中にCodex本文が届いた場合は、待機通知の再生完了後に本文を読み上げます。

設定:

```text
ARGOS_CODEX_PROGRESS_VOICE=true
ARGOS_CODEX_PROGRESS_FIRST_DELAY_SECONDS=8
ARGOS_CODEX_PROGRESS_INTERVAL_SECONDS=20
```

## 必要な外部サービス

- stt-gateway: `POST /transcribe`
- tts-filter: `POST /normalize`
- VOICEVOX Engine: `POST /audio_query` と `POST /synthesis`
- Codex CLI: `codex exec` と `codex exec resume`

Codex のセッションIDは `CODEX_HOME/argos-sessions.json` にスロットごとに保存します。`--json` の標準出力にセッションIDが出ない場合は、`CODEX_HOME/sessions` の直近セッションファイルからIDを補完します。サービス再起動後も保存済みIDを使って同じセッションを再開します。`/reset` を入力すると、現在スロットの保存済みIDも削除します。

外部仕様と設定の詳細は [docs/basic_design.md](docs/basic_design.md) を参照してください。
