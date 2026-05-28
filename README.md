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
- 短押し2回: Codex スロット切替
- 処理中に押下: 音声入出力キャンセル

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

## 必要な外部サービス

- stt-gateway: `POST /transcribe`
- tts-filter: `POST /normalize`
- VOICEVOX Engine: `POST /audio_query` と `POST /synthesis`
- Codex CLI: `codex exec` と `codex exec resume`

外部仕様と設定の詳細は [docs/basic_design.md](docs/basic_design.md) を参照してください。
