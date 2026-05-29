# 基本設計

## 外部仕様

### stt-gateway

`POST /transcribe` に multipart で WAV を送信する。

リクエスト:

- `file`: WAV ファイル
- `language`: 既定値 `ja`

レスポンス:

```json
{
  "ok": true,
  "text": "こんにちは",
  "language": "ja",
  "duration": 1.23,
  "latency_ms": 240,
  "model": "large-v3-turbo"
}
```

### tts-filter

`POST /normalize` に JSON を送信する。

ヘッダー:

- `Authorization: Bearer <TTS_FILTER_BEARER_TOKEN>`
- `Content-Type: application/json`

リクエスト:

```json
{"text": "README.md と LLM"}
```

レスポンス:

```json
{"normalized": "リードミー エムディー と エルエルエム"}
```

### VOICEVOX

VOICEVOX Engine は次の順で呼び出す。

1. `POST /audio_query?text=<text>&speaker=<speaker>`
2. `POST /synthesis?speaker=<speaker>`

`audio_query` の JSON に `outputSamplingRate` を設定してから `synthesis` に渡す。

### Codex CLI

初回発話:

```bash
codex exec --skip-git-repo-check -C <cwd> -s <sandbox> -o <output> -
```

同一スロットの継続発話:

```bash
codex exec resume --last --all --skip-git-repo-check -o <output> -
```

Codex が質問を返した場合は、その応答を読み上げる。次回の PTT 入力は同じスロットの継続発話として送られるため、音声で回答できる。

`codex exec` には対話版の `-a/--ask-for-approval` は渡さない。初回のみ `-C` と `-s` を指定し、継続時は `codex exec resume` の対応オプションだけを使う。

ARGOS は `--json` を強制して Codex CLI の JSONL イベントを読み取る。`agent_message` または `task_complete` から応答テキストを抽出し、既に処理済みの文字列との差分だけをアプリへ渡す。

Codex CLI が最終回答前に途中イベントを出す場合、ARGOS はその差分を順次処理する。CLI 側が最終回答まで応答テキストを出さない場合、完全なトークンストリーミングにはならないが、最終回答の読み上げは句読点単位で分割される。

Codex 呼び出し直後は、ARGOS が短い進捗メッセージを読み上げる。応答本文が届く前に待機時間が長くなった場合は、`ARGOS_CODEX_PROGRESS_FIRST_DELAY_SECONDS` 後から `ARGOS_CODEX_PROGRESS_INTERVAL_SECONDS` 間隔で追加の待機メッセージを読み上げる。メッセージはAI名を出さず、「今やってるから、少し待ってね」のように音声で聞きやすい親しみのある言い方を複数候補からランダムに選ぶ。応答本文の差分が届いた時点で進捗メッセージは停止し、進捗メッセージの再生完了を待ってから通常の応答読み上げに切り替える。

## 状態遷移

- `IDLE`: 待機
- `LISTENING`: PTT 押下中、録音中
- `BUSY`: STT、Codex、TTS の処理中

短押し2回は録音として扱わず、Codex スロット切替に使う。

`BUSY` 中にPTTを押した場合は、再生中の音声をキャンセルしてすぐ `LISTENING` に遷移する。ユーザがそのまま押し続けると録音を継続し、PTT解放時に通常どおり文字起こしとCodex実行へ進む。

## systemd ユニット

`systemd/argos.service` は Raspberry Pi 上で ARGOS を常駐させるための配布用ユニットファイルである。

- `User=pi`、`Group=pi` で実行する
- `WorkingDirectory=/home/pi/argos` を作業ディレクトリにする
- `EnvironmentFile=/home/pi/argos/.env` から設定を読み込む
- `ExecStart=/home/pi/argos/.venv/bin/argos` でプロジェクトの仮想環境内コマンドを起動する
- `PATH` に `/home/pi/.local/bin` を含め、Codex CLI を解決できるようにする
- `network-online.target` と `sound.target` の後に起動する
- 異常終了時は `Restart=on-failure` で再起動する

実運用前に `uv sync` で `.venv/bin/argos` を作成し、`.env` を実機向けに設定する。GPIO や音声デバイスへのアクセスで権限エラーが出る場合は、`pi` ユーザを Raspberry Pi 側の `gpio` や `audio` グループに追加してから再ログインする。

## 音声入力の設定

`AUDIO_DEVICE` は `cat /proc/asound/cards` に表示されるカード名に合わせる。

例:

```text
2 [H2]: USB-Audio - HyperX SoloCast 2
```

この場合の設定:

```text
AUDIO_DEVICE=plughw:CARD=H2,DEV=0
```

`arecord -l` が空でも、`/proc/asound/cards` に USB マイクが見えていればカード名指定で録音できる場合がある。

## 読み上げ分割

Codex 応答は `TextChunker` で区切り文字ごとに分割する。既定の区切り文字は次の通り。

- `。`
- `！`
- `？`
- `!`
- `?`
- 改行

`.` は `README.md` や `systemd.service` などを途中で分割しないよう、既定では区切り文字に含めない。

区切り文字は `ARGOS_TTS_DELIMITERS` で変更できる。例えば読点やコンマでも分割する場合は次のように設定する。

```text
ARGOS_TTS_DELIMITERS=。！？!?、，
```

改行は `ARGOS_TTS_DELIMITERS` の値に関係なく常に区切りとして扱う。

分割済みチャンクは TTS キューに投入する。VOICEVOX 合成と再生はワーカースレッドで順番に行い、Codex の JSONL 読み取りを再生待ちで止めない。キャンセル世代が変わった場合、ワーカーは未処理キューを破棄し、古いチャンクを再生しない。

## Codex スロット

`ARGOS_CODEX_SLOT_N` で複数のスロットを定義する。

書式:

```text
名前,cwd,codex_home,model
```

- `名前`: 読み上げるスロット名
- `cwd`: Codex の作業ディレクトリ
- `codex_home`: 任意。指定した場合は `CODEX_HOME` に設定する
- `model`: 任意。指定した場合は `-m` に渡す

`codex_home` を分けると、`resume --last` の履歴をスロットごとに分離できる。
