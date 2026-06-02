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

`audio_query` の JSON に `outputSamplingRate` と `VOICEVOX_SPEED_SCALE` で指定した `speedScale` を設定してから `synthesis` に渡す。

### ST7789 LCD

`ARGOS_LCD_ENABLED=true` の場合、ARGOS は読み上げる文を ST7789 LCD にも表示する。物理解像度は既定で 76x284 とし、横向き表示になるよう描画内容を90度回転して転送する。日本語フォントは IPA Gothic、IPA P Gothic、IPAex Gothic の順に探し、`ARGOS_LCD_FONT_PATH` が指定されている場合はそれを優先する。IPA系フォントが見つからない場合、LCD表示だけを無効化する。夜間でも明るくなりすぎないよう、ST7789 の色反転を無効にして黒背景に白文字で表示する。

### Codex CLI

初回発話:

```bash
codex exec --skip-git-repo-check -C <cwd> -s <sandbox> -o <output> -
```

同一スロットの継続発話:

```bash
codex exec resume --all --skip-git-repo-check -o <output> <session_id> -
```

ARGOS は Codex CLI の `session_meta.payload.id` を読み取り、`CODEX_HOME` 直下の `argos-sessions.json` にスロットごとに保存する。Codex CLI の標準出力に `session_meta` が出ない場合は、`CODEX_HOME/sessions` の直近セッションファイルから同じ `cwd` のセッションIDを補完して保存する。サービス再起動後は保存済みのセッションIDを指定して `codex exec resume` を実行する。保存済みIDがない実行中プロセス内の継続発話では、従来どおり `--last --all` を使う。

起動時と Codex CLI 実行時には、スロット名、セッション保存先、`CODEX_HOME`、実行コマンド、保存済みセッションIDの有無をログに出す。Codex CLI から新しいセッションIDを受け取った場合、またはセッションファイルから補完した場合も、保存先と合わせてログに出す。

Codex が質問を返した場合は、その応答を読み上げる。次回の PTT 入力は同じスロットの継続発話として送られるため、音声で回答できる。

`codex exec` には対話版の `-a/--ask-for-approval` は渡さない。初回のみ `-C` を指定し、サンドボックスを使う場合は `-s` も指定する。継続時は `codex exec resume` の対応オプションだけを使う。`/reset` ではメモリ上の継続状態と保存済みセッションIDの両方を削除する。

GPIO や SPI などのホストデバイス操作が必要な場合は、`ARGOS_CODEX_BYPASS_SANDBOX=true` で Codex CLI に `--dangerously-bypass-approvals-and-sandbox` を渡す。この設定では `-s` を渡さず、Codex CLI 側のサンドボックスと承認確認を使わない。

ARGOS は `--json` を強制して Codex CLI の JSONL イベントを読み取る。`agent_message` または `task_complete` から応答テキストを抽出し、既に処理済みの文字列との差分だけをアプリへ渡す。

### HDMI ダッシュボード

`ARGOS_DASHBOARD_ENABLED=true` の場合、ARGOS はHTTPサーバーを起動する。ダッシュボード画面は1920x440の横長HDMI画面を基本とし、ARGOS状態、会話履歴、外部通知を3列で表示する。画面幅が狭い場合は通知欄を下へ回り込ませる。

画面更新には Server-Sent Events を使う。外部サービスは `POST /api/events` へ表示イベントを送信する。更新系APIは `ARGOS_DASHBOARD_TOKEN` によるBearer認証を必須とする。通知ではテキスト、画像URL、リンクURLを扱える。将来、GPS検索、メール、Slack、車両情報などを別サービスとして追加するときは、このAPIへ表示イベントを送る。
ARGOS 起動時はステータスを `booting` にして、HDMIダッシュボードへスプラッシュアニメーションを表示する。起動音はVOICEVOXに依存しない合成WAVを生成し、既存の音声出力先へ再生する。
画面は会話、状態、通知を分けて差分描画する。会話ストリーミング中も通知画像のDOMを維持し、不要な再取得とちらつきを防ぐ。
中央の会話欄には保持している会話履歴を表示し、タッチ操作による縦スクロールを有効にする。末尾を表示している場合のみ、新しい会話へ自動追従する。
文字起こし、Codex、TTSフィルター、VOICEVOX、音声再生で内部エラーが起きた場合は、通知欄へ優先度 `high` の通知を追加する。直前と同一のエラーは重複追加しない。

キオスク表示は `argos-dashboard-kiosk.service` をユーザーsystemdへインストールして常駐させる。Chromiumが異常終了した場合は自動再起動する。キオスク画面では管理ポリシー `TranslateEnabled=false` で翻訳UIを無効化し、ダッシュボード上のマウスカーソルを非表示にする。
タッチパネルはlabwcでHDMI画面へ割り当て、`mouseEmulation=no` にしてタッチ操作によるマウスカーソル表示を抑止する。

カメラ静止画は `/tmp/argos/camera-latest.jpg` に保存する。ダッシュボードHTTPサーバーは `/camera/latest.jpg` で最新画像を配信する。

Codex CLI が最終回答前に途中イベントを出す場合、ARGOS はその差分を順次処理する。CLI 側が最終回答まで応答テキストを出さない場合、完全なトークンストリーミングにはならないが、最終回答の読み上げは句読点単位で分割される。

Codex 呼び出し直後は、ARGOS が短い進捗メッセージを読み上げる。応答本文が届く前に待機時間が長くなった場合は、`ARGOS_CODEX_PROGRESS_FIRST_DELAY_SECONDS` 後から `ARGOS_CODEX_PROGRESS_INTERVAL_SECONDS` 間隔で追加の待機メッセージを読み上げる。メッセージはAI名を出さず、「確認するね」や「もう少し待ってね」のように音声で聞きやすい短い言い方を複数候補からランダムに選ぶ。応答本文の差分が届いた時点で進捗メッセージは停止し、進捗メッセージの再生完了を待ってから通常の応答読み上げに切り替える。

### 発話時の挨拶

ARGOS は最初の発話処理時と正常終了時に最終利用時刻を `ARGOS_GREETING_STATE_PATH` のJSONへ保存する。発話処理時は前回利用時刻と現在時刻から挨拶を選ぶ。起動しただけでは挨拶しない。

- 前回利用から10分未満: 挨拶なし
- 同日で10分以上3時間未満: `おかえり。`
- 同日で3時間以上: `久しぶり。お疲れさま。`
- 初回または日付変更後: 時間帯に応じて `おはよう。`、`こんにちは。`、`こんばんは。`

### 本人確認

`ARGOS_AUTH_ENABLED=true` の場合、ARGOS は本人確認が済むまで発話をCodexへ送らない。ロック中の発話は文字起こしだけに使い、`ARGOS_AUTH_KEYWORD_HASH` と一致した場合にロックを解除する。解除キーワードそのものはCodexへ送らない。

音声キーワードはPBKDF2ハッシュとして保存する。ハッシュ作成は `uv run scripts/hash-auth-keyword.py` を使う。認証済みの有効期限は `ARGOS_AUTH_TRUST_SECONDS` で指定し、既定は30分とする。連続失敗が `ARGOS_AUTH_FAILURE_THRESHOLD` に達した場合は警戒通知を出す。

`ARGOS_AUTH_FACE_ENABLED=true` の場合、起動時とロック中の発話時にカメラ照合を試す。照合に成功した場合は音声キーワード解除と同じく認証済み状態へ遷移し、その発話をCodexへ送る。照合に失敗した場合は、同じ発話を音声キーワードとして検証する。

顔サンプル登録は `uv run scripts/enroll-face-auth.py --count 5` で行う。撮影は `ARGOS_AUTH_FACE_CAPTURE_COMMAND` を使い、登録サンプルは `ARGOS_AUTH_FACE_SAMPLES_DIR` に保存する。現段階の顔照合はローカル画像指紋の簡易比較で、しきい値は `ARGOS_AUTH_FACE_THRESHOLD`、必要一致数は `ARGOS_AUTH_FACE_MIN_MATCHES` で調整する。

起動後に未認証の場合は、まず「本人確認をしてください。」と案内する。`ARGOS_AUTH_WARNING_DELAY_SECONDS` の秒数が過ぎても未認証なら、警告音と本人確認案内を `ARGOS_AUTH_WARNING_INTERVAL_SECONDS` 間隔で繰り返す。`ARGOS_AUTH_ALERT_DELAY_SECONDS` を超えたら、ダッシュボード状態を `alert`、表示名を `警戒中` にして「警戒モードに入りました。本人確認してください。」と案内する。本人確認に成功したら警告音タイマーを停止する。本人確認の連続失敗がしきい値に達した場合も同じく警戒状態へ切り替える。

本人確認の連続失敗がしきい値に達した場合は、ダッシュボードへ警戒通知を出し、`ARGOS_AUTH_ALERT_COMMAND` が設定されていれば外部コマンドを実行する。コマンドには `{source}`、`{message}`、`{image_path}` を埋め込める。Slack、SMS、電話などはこのコマンド先のスクリプトで実装する。

## 状態遷移

- `IDLE`: 待機
- `LISTENING`: PTT 押下中、録音中
- `BUSY`: STT、Codex、TTS の処理中

短押し1回は録音を破棄する。短押し2回は録音として扱わず、Codex スロット切替に使う。

`BUSY` 中にPTTを押した場合は、再生中の音声をキャンセルしてすぐ `LISTENING` に遷移する。短押しで離した場合は録音を破棄し、TTSキャンセルだけの操作として扱う。ユーザがそのまま押し続けると録音を継続し、PTT解放時に通常どおり文字起こしとCodex実行へ進む。

前回のSTT、Codex、TTS処理が終了したときは、状態がまだ `BUSY` の場合だけ `IDLE` に戻す。処理終了と同じタイミングで次のPTT録音が始まって `LISTENING` になっている場合は、その状態を維持して解放イベントで録音を停止できるようにする。

GPIO入力は gpiozero のコールバックに処理を直接ぶら下げず、ポーリングした押下/解放エッジをキューに積み、別スレッドで順番にアプリへ渡す。これにより、録音開始やキャンセル処理中でも物理解放イベントを取り逃がしにくくする。

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
