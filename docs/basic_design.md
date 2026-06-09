# 基本設計

## 外部仕様

### stt-gateway

`POST /transcribe` に multipart で WAV を送信する。

リクエスト:

- `file`: WAV ファイル
- `language`: 既定値 `ja`
- `Authorization: Bearer <STT_GATEWAY_BEARER_TOKEN>`。トークン未設定時は送信しない。

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

`STT_GATEWAY_URL` が空の場合は stt-gateway を使わず、faster-whisper でローカル文字起こしを行う。`STT_GATEWAY_URL` が設定済みでも、stt-gateway でエラーが起きた場合はダッシュボードに `stt-gateway` エラーを通知し、その録音を faster-whisper で文字起こしする。

faster-whisper は `ARGOS_WHISPER_MODEL_SIZE`、`ARGOS_WHISPER_DEVICE`、`ARGOS_WHISPER_COMPUTE_TYPE` で調整する。既定モデルは `small`。faster-whisper を使う環境では `uv sync --extra whisper` を実行する。

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

`VOICEVOX_URL` が空の場合は VOICEVOX を使わず、Kokoro TTS で日本語音声を生成する。`VOICEVOX_URL` が設定済みでも、`audio_query` または `synthesis` でエラーが起きた場合はダッシュボードに `VOICEVOX` エラーを通知し、その発話を Kokoro TTS で読み上げる。

Kokoro TTS は `ARGOS_KOKORO_VOICE`、`ARGOS_KOKORO_SPEED`、`ARGOS_KOKORO_REPO_ID`、`ARGOS_KOKORO_SAMPLE_RATE` で調整する。Kokoro を使う環境では `uv sync --extra kokoro` を実行し、必要に応じて `uv run python -m unidic download` で日本語辞書を用意する。

### ST7789 LCD

`ARGOS_LCD_ENABLED=true` の場合、ARGOS は読み上げる文を ST7789 LCD にも表示する。物理解像度は既定で 76x284 とし、横向き表示になるよう描画内容を90度回転して転送する。日本語フォントは IPA Gothic、IPA P Gothic、IPAex Gothic の順に探し、`ARGOS_LCD_FONT_PATH` が指定されている場合はそれを優先する。IPA系フォントが見つからない場合、LCD表示だけを無効化する。夜間でも明るくなりすぎないよう、ST7789 の色反転を無効にして黒背景に白文字で表示する。

HDMIダッシュボードは、現在の状態、現在のエージェントスロット名、provider、会話履歴、通知を表示する。PTT短押しで録音を破棄した場合や、PTTダブルクリックでスロットを切り替えた場合は、認証状態に応じて表示を待機中またはロック中へ戻し、録音中表示を残さない。本人確認が必要なロック中にPTTを押した場合は、通常の録音中や文字起こし中ではなくロック中表示を維持し、発話が本人確認用であることを画面上でも示す。

### LLM エージェント

ARGOS 本体は `AgentClient` インターフェース越しにLLMエージェントへ発話を送る。プロバイダーは `ARGOS_AGENT_PROVIDER` で選択し、現在の既定値は `codex` とする。`codex`、`antigravity`、`hermes` を指定できる。未対応のプロバイダーが指定された場合は起動時にエラーにする。

Codex、Antigravity、Hermes、将来の別エージェントはこの層の実装として追加する。常駐プロセスが必要なエージェントは、今後 `AgentClient` の実装内でプロセス維持や別通信方式を扱い、ARGOS 本体のSTT、TTS、認証、ダッシュボード処理からは隠蔽する。

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
中央の会話欄には保持している会話履歴を表示し、タッチ操作による縦スクロールを有効にする。会話履歴は現在のエージェントスロットごとに分けて保持し、スロット切替と同時に中央の会話欄もそのスロットの履歴へ切り替える。左側パネルにはスロット一覧をARGOSロゴ直下の横並びチップとして表示し、現在スロット、処理中スロット、未読応答があるスロットを見分けられるようにする。スロット数が増えた場合は、左側パネルの縦方向を圧迫しないよう、スロット一覧だけをタッチ操作で横スクロールできるようにする。現在表示していないスロットの応答は中央の会話履歴へ保存するが読み上げず、応答完了時に未読表示と通知を出す。PTTダブルクリックでそのスロットへ切り替えたときに未読応答を別スレッドで読み上げ、未読表示を解除する。未読応答の読み上げも通常応答と同じく句読点単位で分割し、シングルタップで現在の読み上げだけを止められるようにする。末尾を表示している場合のみ、新しい会話へ自動追従する。右側の通知欄も保持している通知を新しい順に全件表示し、タッチ操作による縦スクロールを有効にする。
`ARGOS_DASHBOARD_SCREENSAVER_SECONDS` で指定した秒数だけ画面操作がない場合、ダッシュボードは全画面の黒いオーバーレイを表示する。0以下を指定すると無効化する。この段階ではバックライトやHDMI出力は消さず、タッチ、ポインター、キー、ホイール操作、またはPTT録音開始で黒表示を解除する。
左側のブランド領域には読み上げミュートボタンを表示する。ボタンはARGOSロゴ行の右端に置き、角丸の小型ボタンとして表示する。通常時の文言は「ミュート」とし、薄いグレーで表示する。ミュート中は文言を「ミュート中」に変え、黄色の枠で強調する。操作は `POST /api/control` で受け付け、`mute`、`unmute`、`toggle_mute` をサポートする。このAPIも `ARGOS_DASHBOARD_TOKEN` によるBearer認証を必須とする。ミュートON時は再生中の音声を停止し、TTSワーカーは次のチャンク再生前に待機する。解除後はキューに残っている読み上げを再開する。音声コマンドによるミュート切替は行わない。ミュート状態はボタン表示で示し、録音中、考え中、読み上げ中などの動作ステータスは上書きしない。
左側パネルの左端には読み上げ音量の縦スライダーを表示する。スライダーは `POST /api/control` に `{"action":"set_volume","volume":0..100}` を送信し、ARGOS本体の `AudioPlayer` が16bit PCM WAVを小分けに再生しながらソフトウェア音量を反映する。これにより `plughw` 直指定でALSAミキサーを通らない出力でも、再生中の次の小さい再生ブロックから読み上げ音量を変更できる。ALSAミキサー操作は `AUDIO_OUTPUT_CARD` が設定されている場合はそのカード、未設定の場合はデフォルトミキサーへベストエフォートで送る。起動時は `AUDIO_OUTPUT_VOLUME` を初期値としてダッシュボード状態へ配信する。
文字起こし、LLMエージェント、TTSフィルター、VOICEVOX、音声再生で内部エラーが起きた場合は、通知欄へ優先度 `high` の通知を追加する。直前と同一のエラーは重複追加しない。

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

音声キーワードはPBKDF2ハッシュとして保存する。ハッシュ作成は `uv run scripts/hash-auth-keyword.py` を使う。認証済みの有効期限は `ARGOS_AUTH_TRUST_SECONDS` で指定し、既定は30分とする。待機中に有効期限が切れた場合は、HDMIダッシュボードの状態表示を自動で `ロック中` へ戻す。連続失敗が `ARGOS_AUTH_FAILURE_THRESHOLD` に達した場合は警戒通知を出す。

`ARGOS_AUTH_FACE_ENABLED=true` の場合、起動時とロック中の発話時にカメラ照合を試す。照合に成功した場合は音声キーワード解除と同じく認証済み状態へ遷移し、その発話をCodexへ送る。照合に失敗した場合は、同じ発話を音声キーワードとして検証する。

顔検出確認は `uv run scripts/check-face-detection.py` で行う。撮影画像は `/tmp/argos/camera-latest.jpg` にもコピーする。顔サンプル登録は `uv run scripts/enroll-face-auth.py --count 5` で行う。撮影は `ARGOS_AUTH_FACE_CAPTURE_COMMAND` を使い、登録サンプルは `ARGOS_AUTH_FACE_SAMPLES_DIR` に保存する。登録時は顔が1つだけ検出された画像から、顔領域だけの指紋を保存する。顔検出にはOpenCVを使う。OpenCVが未導入、顔が検出できない、または複数の顔が検出された場合は顔認証を失敗扱いにして音声キーワードへフォールバックする。現段階の顔照合はローカル顔画像指紋の簡易比較で、しきい値は `ARGOS_AUTH_FACE_THRESHOLD`、必要一致数は `ARGOS_AUTH_FACE_MIN_MATCHES` で調整する。

顔認証に失敗し、撮影画像が残っている場合は、画像を `/tmp/argos/camera-latest.jpg` へコピーし、ダッシュボードの通知に `/camera/latest.jpg` として表示する。

`ARGOS_AUTH_FACE_DETECTOR_MODEL_PATH` と `ARGOS_AUTH_FACE_RECOGNIZER_MODEL_PATH` の両方が存在する場合は、OpenCV YuNet で顔検出し、SFace の128次元特徴量で照合する。モデルは `uv run scripts/download-face-models.py` で `~/.local/share/argos/face-models/` に取得する。SFace照合はコサイン類似度を使い、しきい値は `ARGOS_AUTH_FACE_SFACE_THRESHOLD` で指定する。モデルがない場合は従来の明暗指紋方式へフォールバックする。
撮影画像の向きは `ARGOS_AUTH_FACE_IMAGE_ROTATION` で補正する。指定できる値は `0`、`90`、`180`、`270` とする。

起動後に未認証の場合は、まず「本人確認をしてください。」と案内する。`ARGOS_AUTH_WARNING_DELAY_SECONDS` の秒数が過ぎても未認証なら、警告音と本人確認案内を `ARGOS_AUTH_WARNING_INTERVAL_SECONDS` 間隔で繰り返す。`ARGOS_AUTH_ALERT_DELAY_SECONDS` を超えたら、ダッシュボード状態を `alert`、表示名を `警戒中` にして「警戒モードに入りました。本人確認してください。」と案内する。本人確認に成功したら警告音タイマーを停止する。本人確認の連続失敗がしきい値に達した場合も同じく警戒状態へ切り替える。

本人確認の連続失敗がしきい値に達した場合は、ダッシュボードへ警戒通知を出し、`ARGOS_AUTH_ALERT_COMMAND` が設定されていれば外部コマンドを実行する。コマンドには `{source}`、`{message}`、`{image_path}` を埋め込める。Slack、SMS、電話などはこのコマンド先のスクリプトで実装する。

## 状態遷移

- `IDLE`: 待機
- `LISTENING`: PTT 押下中、録音中
- `BUSY`: STT、Codex、TTS の処理中

短押し1回は録音を破棄する。短押し2回は録音として扱わず、Codex スロット切替に使う。

`BUSY` 中にPTTを押した場合は、再生中の音声をキャンセルしてすぐ `LISTENING` に遷移する。短押しで離した場合は録音を破棄し、TTSキャンセルだけの操作として扱う。TTSキャンセル後も実行中エージェントの応答取得とダッシュボードへの保存は継続するため、スロット切替で裏に回った応答も未読として残る。ユーザがそのまま押し続けると録音を継続し、PTT解放時に通常どおり文字起こしとCodex実行へ進む。

前回のSTT、Codex、TTS処理が終了したときは、状態がまだ `BUSY` の場合だけ `IDLE` に戻す。処理終了と同じタイミングで次のPTT録音が始まって `LISTENING` になっている場合は、その状態を維持して解放イベントで録音を停止できるようにする。

GPIO入力は gpiozero のコールバックに処理を直接ぶら下げず、ポーリングした押下/解放エッジをキューに積み、別スレッドで順番にアプリへ渡す。これにより、録音開始やキャンセル処理中でも物理解放イベントを取り逃がしにくくする。

GPIO入力は起動直後の本人確認案内を読み上げる前に初期化する。これにより「本人確認してください」の読み上げ中にPTTを押した場合も、読み上げを止めて録音を開始できる。

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

`AUDIO_DEVICE` は `cat /proc/asound/cards` に表示されるカード名に合わせる。複数のマイク候補を使う場合は `AUDIO_INPUT_DEVICES` にセミコロン区切りで指定する。互換のため `ARGOS_INPUT_DEVICES` と `ARGOS_AUDIO_INPUT_DEVICES` も読み込む。ARGOS は録音開始時に `/proc/asound/cards` を見て、`CARD=...` が接続済みの候補を選ぶ。候補が見つからない場合は `arecord -l` から録音可能デバイスを検出してフォールバックし、それも失敗した場合は先頭候補を使う。

例:

```text
2 [H2]: USB-Audio - HyperX SoloCast 2
```

この場合の設定:

```text
AUDIO_DEVICE=plughw:CARD=H2,DEV=0
AUDIO_INPUT_DEVICES=plughw:CARD=H2,DEV=0;plughw:CARD=Microphone,DEV=0
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

## エージェントスロット

`ARGOS_AGENT_SLOT_N` で複数のスロットを定義する。

書式:

```text
名前,provider,cwd
```

- `名前`: 読み上げるスロット名
- `provider`: `codex` などのエージェント種別
- `cwd`: エージェントの作業ディレクトリ

スロットを指定しない場合は、`ARGOS_AGENT_SLOT_NAME`、`ARGOS_AGENT_PROVIDER`、`ARGOS_AGENT_CWD` から既定スロットを作る。旧 `ARGOS_CODEX_SLOT_N` は互換のため読み込むが、新規設定では `ARGOS_AGENT_SLOT_N` を使う。

Argos が管理するセッションIDは `ARGOS_AGENT_STATE_PATH` に保存する。既定値は `~/.argos/agent-sessions.json` とする。これはCodexの設定ではなくArgos自身の状態なので、`CODEX_HOME` には保存しない。旧 `CODEX_HOME/argos-sessions.json` が存在する場合は互換のため読み込み、保存は新しい `ARGOS_AGENT_STATE_PATH` へ行う。

Codex固有の `CODEX_HOME` とモデルはスロットではなく、`ARGOS_CODEX_HOME` と `ARGOS_CODEX_MODEL` で全体設定として指定する。

## Antigravity CLI

`provider` が `antigravity` のスロットでは、ARGOS は `agy` CLI を1発話ごとに起動する。初回は次の形で実行する。

```bash
agy --print <prompt>
```

`ARGOS_ANTIGRAVITY_CONTINUE_SESSION=true` の場合だけ、同じARGOSプロセス内の継続発話では、Antigravity の `last_conversations.json` から取得した会話IDを使い、次の形で実行する。

```bash
agy --conversation <conversation_id> --print <prompt>
```

`ARGOS_ANTIGRAVITY_COMMAND` で `agy` のパスを指定する。既定値は `/home/yuki/.local/bin/agy` とする。Antigravity のキャッシュは `ARGOS_ANTIGRAVITY_HOME` から読み、既定値は `~/.gemini/antigravity-cli` とする。`ARGOS_ANTIGRAVITY_SKIP_PERMISSIONS=true` の場合は `--dangerously-skip-permissions` を渡す。`ARGOS_ANTIGRAVITY_SANDBOX=true` の場合は `--sandbox` を渡す。

Antigravity は会話再開時に過去の画面出力を標準出力へ混ぜることがある。ARGOS は `agy` の標準出力を回答本文としては使わず、実行後に `transcript_full.jsonl` または `transcript.jsonl` の追加分を読み、末尾から `source=MODEL`、`type=PLANNER_RESPONSE`、`status=DONE`、`content` ありのエントリーだけを回答として扱う。`agy` の標準出力と標準エラーは調査用に `/tmp/argos/antigravity-raw.log` と `/tmp/argos/antigravity-error.log` へ保存する。

既定では毎回新規会話として起動し、`--conversation` は渡さない。会話を継続したい場合だけ `ARGOS_ANTIGRAVITY_CONTINUE_SESSION=true` を指定する。サービス再起動後も保存済み会話IDを復元したい場合は、さらに `ARGOS_ANTIGRAVITY_RESUME_SAVED=true` を指定する。`ARGOS_ANTIGRAVITY_PROMPT_PREFIX` は任意の固定prefixだが、既定では空にする。読み上げ向けの整形はprovider個別ではなく、共通のTTSフィルター側で扱う。

## Hermes Agent CLI

Hermes provider は `hermes chat -q <prompt> -Q --source <source>` を使う。`-Q` によりプログラム向けの出力にし、`ARGOS_HERMES_PASS_SESSION_ID=true` の場合は `--pass-session-id` を渡す。`ARGOS_HERMES_MODEL`、`ARGOS_HERMES_PROVIDER`、`ARGOS_HERMES_TOOLSETS`、`ARGOS_HERMES_SKILLS` はそれぞれ Hermes CLI の `--model`、`--provider`、`--toolsets`、`--skills` に対応する。追加オプションは `ARGOS_HERMES_EXTRA_ARGS` で指定する。

Hermes の session ID は `ARGOS_AGENT_STATE_PATH` にスロットごとに保存する。`ARGOS_HERMES_RESUME_SAVED=true` の場合、保存済みsession IDを起動時に復元し、次回実行では `--resume <session_id>` を渡す。`/reset` では保存済みsession IDを削除し、次回から新規会話として扱う。Hermes側の会話履歴本体は Hermes の管理領域に保存され、ARGOS はsession IDだけを保持する。
