# 基本設計

## 外部仕様

### stt-gateway

`POST /transcribe` に multipart で WAV を送信する。

リクエスト:

- `file`: WAV ファイル（`STT_GATEWAY_USE_OPUS=true` のときは Opus ファイル）
- `language`: 既定値 `ja`
- `Authorization: Bearer <STT_GATEWAY_BEARER_TOKEN>`。トークン未設定時は送信しない。

`STT_GATEWAY_USE_OPUS=true` のときは、録音WAVを ffmpeg で Ogg Opus にエンコードしてから送信し、アップロードサイズを削減する。送信ファイル名は拡張子を `.opus` に、MIME は `audio/opus` にする。ビットレートは `STT_GATEWAY_OPUS_BITRATE`（既定 `24k`）で調整する。stt-gateway 側が Opus 受信に対応している必要がある。既定（`false`）では従来どおり WAV を送る。

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
文字起こし結果が空文字だった場合は発話をエージェントへ送らず、ログに録音ファイルパスとRMSを出し、ダッシュボード通知に「音声を認識できませんでした。」を表示する。

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

ARGOS本体は読み上げ文をローカル補正せず、tts-filter へそのまま渡す。読み上げ辞書や読み間違い補正は tts-filter 側で管理する。tts-filter に接続できない場合は、元のテキストをそのまま返す。
`argos-install --apply` または `--update` は、ARGOS本体の `.env` と `services/tts-filter/.env` の `TTS_FILTER_BEARER_TOKEN` を同じ値に揃える。両方が未設定または `change-me` の場合はランダムなトークンを生成する。Bearerトークンを含む `.env` は、インストーラーが所有者のみ読み書き可能（600）へ権限を絞る。

### VOICEVOX

VOICEVOX Engine は次の順で呼び出す。

1. `POST /audio_query?text=<text>&speaker=<speaker>`
2. `POST /synthesis?speaker=<speaker>`

`audio_query` の JSON に `outputSamplingRate` と `VOICEVOX_SPEED_SCALE` で指定した `speedScale` を設定してから `synthesis` に渡す。

`VOICEVOX_BEARER_TOKEN` が設定されている場合は、`audio_query` と `synthesis` の両方へ `Authorization: Bearer <token>` を付ける。未設定の場合は認証ヘッダーを送らない。

`VOICEVOX_ACCEPT_OPUS=true` のときは、`synthesis` のリクエストに `Accept: audio/opus` を付ける。素の VOICEVOX Engine は Opus 非対応のため、これは Opus 対応ラッパーを前提とする（ラッパーが `Accept` を見て Ogg Opus を返す）。レスポンスの `Content-Type` に `opus` を含む場合は ffmpeg で WAV へデコードしてから再生へ渡す。WAV が返った場合はそのまま再生するため、既定（`false`）や非対応エンジンでもフォールバックが効く。

`VOICEVOX_URL` が空の場合は VOICEVOX を使わず、Kokoro TTS で日本語音声を生成する。`VOICEVOX_URL` が設定済みでも、`audio_query` または `synthesis` でエラーが起きた場合はダッシュボードに `VOICEVOX` エラーを通知し、その発話を Kokoro TTS で読み上げる。

Kokoro TTS は `ARGOS_KOKORO_VOICE`、`ARGOS_KOKORO_SPEED`、`ARGOS_KOKORO_REPO_ID`、`ARGOS_KOKORO_SAMPLE_RATE` で調整する。Kokoro を使う環境では `uv sync --extra kokoro` を実行し、必要に応じて `uv run python -m unidic download` で日本語辞書を用意する。

### TTSキャッシュ

ARGOS は短い読み上げ文の合成結果をWAVファイルとしてローカルにキャッシュする。既定では30文字以下を `cache/tts` に保存し、同じテキストとVOICEVOX話者IDの組み合わせを再利用する。上限容量は `ARGOS_TTS_CACHE_MAX_SIZE_MB` で制御し、超過時は最終アクセス時刻が古いファイルから削除する。

設定は `ARGOS_TTS_CACHE_ENABLED`、`ARGOS_TTS_CACHE_MAX_CHARS`、`ARGOS_TTS_CACHE_MAX_SIZE_MB`、`ARGOS_TTS_CACHE_DIR` で変更できる。VOICEVOX失敗時にKokoroへフォールバックした音声も、同じキーでキャッシュする。

### ST7789 LCD

`ARGOS_LCD_ENABLED=true` の場合、ARGOS は読み上げる文を ST7789 LCD にも表示する。物理解像度は既定で 76x284 とし、横向き表示になるよう描画内容を90度回転して転送する。日本語フォントは IPA Gothic、IPA P Gothic、IPAex Gothic の順に探し、`ARGOS_LCD_FONT_PATH` が指定されている場合はそれを優先する。IPA系フォントが見つからない場合、LCD表示だけを無効化する。夜間でも明るくなりすぎないよう、ST7789 の色反転を無効にして黒背景に白文字で表示する。

HDMIダッシュボードは、現在の状態、現在のエージェントスロット名、provider、Wi-Fi接続状態、会話履歴、通知を表示する。PTT短押しで録音を破棄した場合や、PTTダブルクリックでスロットを切り替えた場合は、認証状態に応じて表示を待機中またはロック中へ戻し、録音中表示を残さない。本人確認が必要なロック中にPTTを押した場合は、通常の録音中ではなく「本人確認録音中」と表示し、PTT解放後は「本人確認中」と表示する。これにより、発話が本人確認用であることと、PTT入力が認識されたことを画面上でも示す。

### LLM エージェント

ARGOS 本体は `AgentClient` インターフェース越しにLLMエージェントへ発話を送る。プロバイダーは `ARGOS_AGENT_PROVIDER` で選択し、現在の既定値は `codex` とする。`codex`、`antigravity`、`hermes`、`claude` を指定できる。未対応のプロバイダーが指定された場合は起動時にエラーにする。

Codex、Antigravity、Hermes、将来の別エージェントはこの層の実装として追加する。常駐プロセスが必要なエージェントは、今後 `AgentClient` の実装内でプロセス維持や別通信方式を扱い、ARGOS 本体のSTT、TTS、認証、ダッシュボード処理からは隠蔽する。

`ARGOS_AGENT_RUNNER_URL` が設定されている場合、ARGOS 本体は Codex、Antigravity、Hermes を直接起動せず、Agent Runner HTTP APIへジョブを作成する。Runnerは `argos-agent-runner` コマンドで別プロセスとして起動し、`ARGOS_AGENT_RUNNER_HOST`、`ARGOS_AGENT_RUNNER_PORT` で待ち受ける。更新系APIは `ARGOS_AGENT_RUNNER_TOKEN` によるBearer認証に対応する。

Agent Runner はジョブごとに `ARGOS_AGENT_RUNNER_STATE_DIR/jobs/<job_id>/` を作成し、`job.json`、`prompt.txt`、`output.txt`、`result.txt`、`error.txt` を保存する。ジョブ状態は `queued`、`running`、`completed`、`delivered`、`failed`、`failed_delivered`、`cancelled` を使い、処理完了とARGOS本体への配信済み状態を分ける。これにより、ARGOS本体が再起動してもRunner側の実行結果を後から確認でき、配信済み結果を起動のたびに繰り返し読み上げる事故を避ける。

`output.txt` は実行中も逐次flushされ、`GET /api/jobs/<job_id>` のレスポンスに `output` フィールドとして含まれる。ARGOS本体側の `RunnerAgentClient.ask_stream` は0.2秒ごとにポーリングし、前回までに受け取った `output` との差分だけを読み上げ用チャンクとして返す。これにより、Runner経由でもCLI側のトークン単位ストリーミングをほぼそのまま中継できる。

同一スロット（同じ会話セッション）で現在のRunnerプロセスが `running`/`queued` 状態のジョブを実行中の場合、`AgentRunner.start_job` は新規ジョブを作らず `409 Conflict` を返す。既存ジョブを返してしまうと、新しいユーザー発話が古いジョブに吸収されて失われるため、競合として明示的に失敗させる。ARGOS本体からのHTTPリクエストが一時的なタイムアウトで失敗しても、Runner側のジョブ自体はバックグラウンドスレッドで動き続けているため、同じ会話セッションへ`claude --resume`等のCLIプロセスを複数同時に走らせない。

Runner起動時に状態ディレクトリへ `running`/`queued` のジョブが残っている場合、それらは前回Runnerプロセスの再起動や異常終了で実行スレッドを失った中断ジョブとして `failed` に更新する。これにより、古い `running` 状態が永続化されたまま新しい発話を塞ぎ続ける事故を避ける。また `RunnerAgentClient` のポーリングは、Raspberry Pi側の一時的な負荷などでHTTPリクエストが失敗しても連続10回までは諦めずに再試行し、ジョブ自体が生きていれば応答取得失敗として扱わない。

初期版のRunnerクライアントは、ジョブ完了までポーリングして最終結果をARGOS本体へ返す。ARGOS本体は起動中、Runnerの未配信完了ジョブを定期的に確認し、見つけた結果を該当スロットの会話履歴へ追加し、通知を出してから配信済みにする。回収した結果が現在スロットなら自動で読み上げ、別スロットなら未読表示にしてスロット切替時に読み上げる。共通システムプロンプトを付与するラッパーは、Runnerの未配信回収APIを実クライアントへ透過的に委譲し、本体再起動後の結果回収を妨げない。通常のTTSキャンセルやスロット切替ではRunnerジョブを停止しない。明示的なジョブキャンセルAPIは今後の拡張点とする。セッションリセットは `POST /api/slots/reset` で現在スロットの保存済みセッションIDをRunner側から削除する。

### Codex CLI

初回発話:

```bash
codex exec --skip-git-repo-check -C <cwd> -s <sandbox> -o <output> -
```

同一スロットの継続発話:

```bash
codex exec resume --all --skip-git-repo-check -o <output> <session_id> -
```

ARGOS は Codex CLI の `thread.started` イベントの `thread_id` をセッションIDとして読み取り、`CODEX_HOME` 直下の `argos-sessions.json` にスロットごとに保存する（旧バージョンの `session_meta.payload.id` 形式にも互換対応する）。Codex CLI の標準出力からセッションIDを取得できない場合は、`CODEX_HOME/sessions` の直近セッションファイルから同じ `cwd` のセッションIDを補完して保存する。サービス再起動後は保存済みのセッションIDを指定して `codex exec resume` を実行する。保存済みIDがない実行中プロセス内の継続発話では、従来どおり `--last --all` を使う。

起動時と Codex CLI 実行時には、スロット名、セッション保存先、`CODEX_HOME`、実行コマンド、保存済みセッションIDの有無をログに出す。Codex CLI から新しいセッションIDを受け取った場合、またはセッションファイルから補完した場合も、保存先と合わせてログに出す。

Codex が質問を返した場合は、その応答を読み上げる。次回の PTT 入力は同じスロットの継続発話として送られるため、音声で回答できる。

`codex exec` には対話版の `-a/--ask-for-approval` は渡さない。初回のみ `-C` を指定し、サンドボックスを使う場合は `-s` も指定する。継続時は `codex exec resume` の対応オプションだけを使う。`/reset` ではメモリ上の継続状態と保存済みセッションIDの両方を削除する。

GPIO や SPI などのホストデバイス操作が必要な場合は、`ARGOS_CODEX_BYPASS_SANDBOX=true` で Codex CLI に `--dangerously-bypass-approvals-and-sandbox` を渡す。この設定では `-s` を渡さず、Codex CLI 側のサンドボックスと承認確認を使わない。

ARGOS は `--json` を強制して Codex CLI の JSONL イベントを読み取る。`item.completed`（`item.type == "agent_message"`）から応答テキストを抽出する（旧バージョンの `event_msg`/`response_item` 形式にも互換対応する）。

`ARGOS_CODEX_STREAM_MODE` で読み上げ方式を切り替えられる。

- `stream`（既定）: 上記の差分を逐次読み上げる。完了後に `-o` で受け取った最終出力は、途中経過を一切取得できなかった場合のフォールバックとしてのみ使う。
- `final`: 途中経過は読み上げず、完了後に `-o` で受け取った最終出力をまとめて1回だけ読み上げる。

Codex の最終出力は途中経過の単純な続きではなく、応答全体を再構成したまとめになることがある。`stream` モードで両方読み上げると同じ内容を2回話すことになるため、用途に応じて `final` へ切り替えられるようにしている。この設定は Codex 専用で、Claude CLI には影響しない（Claude CLI は `--include-partial-messages` によるトークン単位の差分のみを使い、完了後の二重読み上げは発生しない）。

### Claude CLI

初回発話:

```bash
claude -p --output-format stream-json --verbose --permission-mode dontAsk --session-id <session_id> "プロンプト"
```

同一スロットの継続発話:

```bash
claude -p --output-format stream-json --verbose --permission-mode dontAsk --resume <session_id> "プロンプト"
```

ARGOS は、最初の開始時またはリセット時に新規の UUID を生成して `--session-id` で起動し、セッションIDをスロットごとに保存する。2回目以降の会話継続時は `--resume <session_id>` を指定して以前の履歴を再開する。
`claude` の実行時は、 `stdin=subprocess.DEVNULL` を指定して完全に非対話（non-interactive）として認識させることで、信頼確認ダイアログなどでブロッキングするのを防ぐ。
ARGOS は、 `--output-format stream-json --verbose` にて出力される NDJSON ストリームの `type == "assistant"` イベントから `content` 内の `text` 差分のみを抽出し、既に処理済みの文字列との差分だけをアプリへ渡す。

### HDMI ダッシュボード

`ARGOS_DASHBOARD_ENABLED=true` の場合、ARGOS はHTTPサーバーを起動する。ダッシュボード画面は1920x440の横長HDMI画面を基本とし、ARGOS状態、会話履歴、外部通知を3列で表示する。800x600程度の画面では左側操作を圧縮した3列表示を維持し、さらに狭い場合だけ通知欄を下へ回り込ませる。また、画面の高さが極端に低い場合（高さ500px以下）は、会話履歴の文字サイズを維持したまま左側操作パネルの余白（マージンやパディング）を自動で縮小し、スロットボタンや操作パネルが画面下に見切れるのを防ぐ仕様とする。

既存の `/` は車載・固定ディスプレイ向け表示として維持する。`/sp` はスマートフォン・タブレット向け表示を提供し、状態欄と通知欄を左右のドロワーとして開閉する。SP表示ではページ全体を固定ビューポートに収め、本文スクロールを禁止する。スクロール対象は会話履歴、状態ドロワー、通知ドロワーに限定する。`VisualViewport` の高さと上端位置を反映し、iOSのソフトウェアキーボード開閉時にも入力欄を表示領域内へ維持する。通知到着時はドロワーを自動表示せず、通知ボタンの未確認件数だけを更新してテキスト入力を妨げない。通知ドロワーを開いた時点で確認済みにし、最新確認時刻をブラウザの `localStorage` に保存する。通知履歴は削除せず、確認状態は端末ごとに独立させる。ダッシュボードHTMLはブラウザへキャッシュさせず、更新後の操作UIを再読み込み時に反映する。

待ち受けアドレスは `ARGOS_DASHBOARD_HOST` で指定する。kioskブラウザは同一機からアクセスするため既定は `127.0.0.1`（localhostのみ）とし、状態・会話履歴・カメラ画像などGET系APIを認証なしでLANへ露出しない。LAN内の別端末から表示させる場合だけ `0.0.0.0` などへ明示的に広げる。

LANへ広げる場合は `ARGOS_DASHBOARD_VIEW_KEY` に閲覧用アクセスキーを設定する。キー設定時は、画面(`/`)、静的ファイル(`/static/*`)、状態(`/api/state`)、位置情報(`/api/location`)、SSE(`/api/stream`)、カメラ画像、アップロード画像の閲覧に認証を必須とする。認証は `?key=<値>` クエリ、発行済みCookie（`argos_view_key`、HttpOnly）、または `ARGOS_DASHBOARD_TOKEN` のBearerヘッダーのいずれかで通す。正しいキー付きで画面を開いた端末にはCookieを配り、以降はキーなしURLで再読込できる。`/api/health` は死活監視用に常時開放する。キー未設定時は従来通り閲覧制限なし（後方互換）。kiosk起動スクリプトは `ARGOS_DASHBOARD_VIEW_KEY` が設定されていれば起動URLへ自動で付与する。ダッシュボードHTMLには更新用Bearerトークンが埋め込まれるため、閲覧認証を通過した端末は更新系APIも利用できる点に注意する（端末別権限分離は分散ARGOS設計で扱う）。

画面更新には Server-Sent Events を使う。外部サービスは `POST /api/events` へ表示イベントを送信する。更新系APIは `ARGOS_DASHBOARD_TOKEN` によるBearer認証を必須とする。通知ではテキスト、画像URL、リンクURLを扱える。インストーラーはARGOS本体の `.env` と `services/argos-reminder/.env` の `ARGOS_DASHBOARD_TOKEN` を同じ値に揃え、リマインダー通知が401で失敗しないようにする。将来、GPS検索、メール、Slack、車両情報などを別サービスとして追加するときは、このAPIへ表示イベントを送る。
通知イベントでは `sound` と `speak` の真偽値を受け付ける。`sound=true` の場合はARGOS本体が通知音を鳴らし、`speak=true` の場合は通知タイトルと本文を読み上げる。どちらかが指定された通知では画面を起こす。通知音と読み上げはHTTP応答を待たせないよう、ARGOS本体側の別スレッドで処理する。

通知イベントには表示位置を指定する `display` を追加する。既定の `toast` は従来どおり右カラムの通知欄に積む。`center` を指定すると、右カラムの通知履歴に加えて画面中央へ大きなアラート（`center_alert`）を重ねて表示する。中央アラートは「ご飯だよ〜」のような全員へ強く見せたい一斉連絡を想定し、画像・大きなタイトル・本文をまとめて中央に出す。`duration_seconds` に正の秒数を指定すると、その秒数の経過後に中央アラートを自動で閉じる。0または未指定の場合は画面タップで閉じるまで残す。中央アラートは画面タップ、`duration_seconds` の経過、または `type:"clear_center_alert"` イベントで消去し、消去時はサーバー状態(`center_alert`)もクリアして再描画で復活しないようにする。中央アラートは iframe オーバーレイのスタックとは独立した専用レイヤで、`snapshot()` の `center_alert` を通じてSSEで配信する。

通知画像は外部URL参照(`image_url`)に加えて、ARGOS本体へアップロードして配信できる。`POST /api/uploads` はBearer認証必須で、`Content-Type` に画像MIME（`image/png`、`image/jpeg`、`image/webp`、`image/gif`）を指定した生ボディを受け取り、`ARGOS_DASHBOARD_UPLOAD_DIR`（既定 `/tmp/argos/uploads`）へ `<UUID>.<拡張子>` として保存し、`{"url": "/uploads/<name>"}` を返す。受信サイズは `ARGOS_DASHBOARD_UPLOAD_MAX_BYTES`（既定5MB）を上限とし、保存件数は `ARGOS_DASHBOARD_UPLOAD_KEEP`（既定50件）を超えた古い画像から削除する。`GET /uploads/<name>` は保存済み画像を配信し、パストラバーサルを防ぐためファイル名にディレクトリ区切りや `..` を含むリクエストは拒否する。送信側は「画像をアップロードして得たURLを通知の `image_url` に入れて `/api/events` へ送る」2段構成で画像付き通知を出す。将来、1リクエストで完結させるための `image_data`(base64) 埋め込みは拡張点として残す。

複数端末への一斉通知は、当面は送信側が各端末の `/api/events` を順に叩くファンアウト方式とし、ARGOS本体には配信・中継機構を持たせない。通知イベントの任意項目 `target`（宛先ラベル）は将来の一斉通知向けに受理して無視するだけとし、バリデーションで弾かない。端末レジストリ（ホスト名・部屋・人）、死活監視、自動登録、宛先グループ、ブロードキャストAPIは今後の拡張点として別途設計する。
ARGOS 起動時はステータスを `booting` にして、HDMIダッシュボードへスプラッシュアニメーションを表示する。起動音はVOICEVOXに依存しない合成WAVを生成し、既存の音声出力先へ再生する。
画面は会話、状態、通知を分けて差分描画する。会話ストリーミング中も通知画像のDOMを維持し、不要な再取得とちらつきを防ぐ。
動作状態は文字だけでなく画面外周の発光枠でも示す。`listening` と `auth_listening` は黄色の明滅枠、`transcribing` は赤橙の流れる枠、`thinking` と `authenticating` は水色の流れる枠、`speaking` は青の枠、`locked`、`alert`、`error` は赤系の枠を表示する。枠はCSS疑似要素で描画し、タッチ操作やオーバーレイ操作を妨げない。枠色はPiSugarモバイル端末（argos-terminal）のLED色と揃えてあり、`transcribing`＝赤橙・`speaking`＝青は端末側と一致する（端末は `thinking` も赤橙で扱う点だけ異なる）。
中央の会話欄には保持している会話履歴を表示し、タッチ操作による縦スクロールを有効にする。会話履歴は現在のエージェントスロットごとに分けて保持し、スロット切替と同時に中央の会話欄もそのスロットの履歴へ切り替える。左側パネルにはスロット一覧をARGOSロゴ直下の横並びチップとして表示し、現在スロット、処理中スロット、未読応答があるスロットを見分けられるようにする。スロット数が増えた場合は、左側パネルの縦方向を圧迫しないよう、スロット一覧だけをタッチ操作で横スクロールできるようにする。現在表示していないスロットの応答は中央の会話履歴へ保存するが読み上げず、応答完了時に未読表示と通知を出す。PTTダブルクリックでそのスロットへ切り替えたときに未読応答を別スレッドで読み上げ、未読表示を解除する。未読応答の読み上げも通常応答と同じく句読点単位で分割し、シングルタップで現在の読み上げだけを止められるようにする。末尾を表示している場合のみ、新しい会話へ自動追従する。右側の通知欄も保持している通知を新しい順に全件表示し、タッチ操作による縦スクロールを有効にする。
`ARGOS_DASHBOARD_SCREENSAVER_SECONDS` で指定した秒数だけ画面操作がない場合、ダッシュボードは全画面の黒いオーバーレイを表示する。0以下を指定すると無効化する。この段階ではバックライトやHDMI出力は消さず、タッチ、ポインター、キー、ホイール操作、PTT録音開始、または音声読み上げ開始で黒表示を解除する。マイクOFF中でPTTを押した場合は、録音や本人確認はしないが黒表示だけは解除する。
左側のブランド領域には読み上げミュートボタンを表示する。ボタンはARGOSロゴ直下の操作行に置き、狭い画面でも折り返して見切れないようにする。通常時の文言は「ミュート」とし、薄いグレーで表示する。ミュート中は文言を「ミュート中」に変え、黄色の枠で強調する。操作は `POST /api/control` で受け付け、`mute`、`unmute`、`toggle_mute` をサポートする。このAPIも `ARGOS_DASHBOARD_TOKEN` によるBearer認証を必須とする。インストーラーは `.env` の `ARGOS_DASHBOARD_TOKEN` が空の場合、`--apply` または `--update` 実行時にランダムなトークンを自動生成する。ミュートON時は再生中の音声を停止し、TTSワーカーは次のチャンク再生前に待機する。解除後はキューに残っている読み上げを再開する。音声コマンドによるミュート切替は行わない。ミュート状態はボタン表示で示し、録音中、考え中、読み上げ中などの動作ステータスは上書きしない。変更したミュート状態は `ARGOS_AUDIO_STATE_PATH` のJSONへ保存し、ARGOS再起動後に復元する。
同じ領域にマイクOFFボタンを表示する。操作は `enable_microphone`、`disable_microphone`、`toggle_microphone` で受け付ける。マイクOFF中はPTT押下とウェイクワード検知による録音を行わず、進行中の録音があれば破棄する。ただしマイクOFF中でもPTT押下でスクリーンセーバー（黒表示）だけは解除する。これは読み上げミュートとは独立した一時停止で、再起動後の永続化はしない。
左側パネルにはフォントサイズ切替ボタンを表示し、ダッシュボードの主要テキストを `小`、`中`、`大` から選べるようにする。選択値はキオスクブラウザのローカルストレージへ保存し、画面再読み込み後も維持する。未保存時は `ARGOS_DASHBOARD_DEFAULT_FONT_SIZE` を初期値にする。切替対象は会話欄、通知欄、現在スロット、状態表示、スロットチップなどの可読性に関わるテキストとする。
左側パネルの `CURRENT SLOT` にはセッションリセットボタンを表示する。誤操作防止のため、1回目のタップで確認表示に切り替え、5秒以内にもう一度タップした場合だけ `POST /api/control` に `{"action":"reset_agent_session"}` を送る。この操作は現在スロットのエージェントセッションIDだけを削除し、ダッシュボードに残っている会話履歴や通知は削除しない。リセット後の次回エージェント呼び出しは新規セッションとして開始し、完了時に通常どおり新しいセッションIDを保存する。
左側パネルの左端には読み上げ音量の縦スライダーを表示する。スライダーは `POST /api/control` に `{"action":"set_volume","volume":0..100}` を送信し、ARGOS本体の `AudioPlayer` が16bit PCM WAVを小分けに再生しながらソフトウェア音量を反映する。これにより `plughw` 直指定でALSAミキサーを通らない出力でも、再生中の次の小さい再生ブロックから読み上げ音量を変更できる。ALSAミキサー操作は `AUDIO_OUTPUT_CARD` が設定されている場合はそのカード、未設定の場合はデフォルトミキサーへベストエフォートで送る。起動時は `ARGOS_AUDIO_STATE_PATH` の保存済み音量を優先し、保存済み音量がない場合だけ `AUDIO_OUTPUT_VOLUME` を初期値として使う。保存値が壊れている場合は無視する。
左側パネルには、時刻、状態、カレントスロットの順で縦並びに表示し、現在スロットのproviderに対応する利用枠取得コマンドが設定されている場合だけ、その真下にLLMエージェント利用枠を表示する。設定名は `ARGOS_AGENT_USAGE_COMMAND_<PROVIDER>` とし、例として `ARGOS_AGENT_USAGE_COMMAND_CODEX`、`ARGOS_AGENT_USAGE_COMMAND_ANTIGRAVITY`、`ARGOS_AGENT_USAGE_COMMAND_CLAUDE` を使える。コマンドは標準出力へJSONを返し、`{"5hour":{"remain_percentage":95.18,"use_percentage":4.82,"reset_at":"06/16 10:01"},"weekly":{"remain_percentage":34.57,"use_percentage":65.43,"reset_at":"06/19 06:59"},"other":{"text":"878 credits"}}` の形式を受け付ける。5時間枠と週の枠については、使用パーセンテージに応じたプログレスバーで表示する。`ARGOS_AGENT_USAGE_REFRESH_SECONDS` 間隔で現在providerだけを取得し、コマンド失敗時はエラーを表示する。取得処理は表示専用で、エージェント実行やリミット制御は行わない。

左側パネルのARGOSロゴ横にはWi-Fi状態をバーアイコンで表示する。ARGOS本体は `/proc/net/wireless` から電波品質を読み、`iwgetid` でSSIDを取得する。更新間隔は `ARGOS_WIFI_STATUS_REFRESH_SECONDS` で指定し、未接続または取得不能の場合はWi-Fi表示自体を出さない。

左側パネルの日付行には現在地に基づく天気と気温を表示する。天気アイコンは端末フォントに依存する絵文字を使わず、CSSで描画する簡易アイコンを使う。
文字起こし、LLMエージェント、TTSフィルター、VOICEVOX、音声再生で内部エラーが起きた場合は、通知欄へ優先度 `high` の通知を追加する。直前と同一のエラーは重複追加しない。また、LLMエージェントからの応答取得でエラーが発生した際は、リミット制限エラー（`rate limit`、`quota`、`limit`など）であれば「リミット制限に達しました。」、その他の一般エラーであれば「エージェントの応答取得に失敗しました。」と音声で読み上げて報告する。

キオスク表示は `argos-dashboard-kiosk.service` をユーザーsystemdへインストールして常駐させる。Chromiumが異常終了した場合は自動再起動する。キオスク画面では管理ポリシーで翻訳UI、Googleサインイン、同期UI、パスワード保存を無効化し、ダッシュボード上のマウスカーソルを非表示にする。起動時は `xset` と `gsettings` を使い、UbuntuとRaspberry Pi OSの両方でスクリーンセーバー、DPMS、ロック画面を可能な範囲で無効化する。
Chromiumはまずローカルの接続待ち画面 `scripts/kiosk-splash.html` を `?target=<ダッシュボードURL>` 付きで開く。接続待ち画面は `/api/health` へ2秒間隔で疎通確認（`no-cors` のため401でも到達扱い）を行い、母艦へ到達できたら `target` のURLへ自動遷移する。これにより起動直後やネットワーク未接続時にChromiumのオフラインエラー画面（操作手段のないキオスクでは復帰不能）が表示されるのを防ぐ。同ファイルはリモートキオスク端末（ginger等）へも配布して同じ方式で使う。
接続待ち画面と後述の再接続オーバーレイは、端末ローカルの状態サーバ（argos-terminal内蔵、`http://127.0.0.1:8899/status`）が存在すればWi-Fi・Tailscale・母艦それぞれの接続状態を○×で表示する（無い環境では欄ごと非表示）。
ダッシュボード自身にも接続断オーバーレイを内蔵する。SSE（`/api/stream`）が切断されて8秒以上復帰しない場合、全画面の「再接続中」表示に切り替え、以後3秒間隔で `/api/health` を確認して届き次第自動リロードで復帰する。これによりキオスク端末は表示中に母艦や経路が落ちてもオフラインエラー画面で詰まらず、自動で接続待ち状態→復帰まで遷移する。
タッチパネルはlabwcでHDMI画面へ割り当て、`mouseEmulation=no` にしてタッチ操作によるマウスカーソル表示を抑止する。

カメラ静止画は `/tmp/argos/camera-latest.jpg` に保存する。ダッシュボードHTTPサーバーは `/camera/latest.jpg` で最新画像を配信する。

#### マルチパネル・ダッシュボードとスロット管理

ダッシュボード画面の右側2カラム（中央カラムと右カラム）は、それぞれ独立した「表示スロット（`center`スロットと `right`スロット）」として構成され、任意のコンテンツ（会話、通知、地図、画像など）を動的にマウント・表示できる。

1. **基本（デフォルト）コンテンツとスタック管理**
   - 各スロットは表示スタック（Stack）を持ち、コンテンツを積み重ねて表示・管理する。
   - スロットの最下層（インデックス0）には、システムが常に更新し続けるデフォルトのコンテンツが固定され、削除（Pop）できない。
     - **中央スロット (`center`)**: 既定値は `conversation` (会話履歴)
     - **右スロット (`right`)**: 既定値は `notifications` (通知履歴)
   - 一時的なコンテンツ（地図、画像、Markdown等）を表示する際は、指定スロットのスタックの最前面（末尾）にコンテンツを積み上げる（Push）。
   - 一時コンテンツを「閉じる」と、最前面のコンテンツが取り除かれ（Pop）、自動的に下層にあるコンテンツ（会話や通知）が再表示される。

2. **スロット操作イベント**
   `POST /api/events` やコントロールAPIから、表示を動的に制御する。
   - **表示イベント (`type: "overlay"`)**:
     - `target_slot`: 表示先スロット（`center` または `right`。省略時は `right`）。
     - `overlay_type`: コンテンツ種別（`map`、`markdown`、`image`、`html` など）。
     - `title`: タイトル。
     - `url`: コンテンツURL。
     - `content` / `options` (任意): コンテンツデータやオプション。
     - `replace_top` (任意): `true` の場合、対象スロットに一時コンテンツが既にあれば最前面を差し替え、スタックを増やさない。ナビ地図のズーム変更など、同じ表示を更新する操作で使う。
   - **消去イベント (`type: "clear_overlay"`)**:
     - `target_slot`: 消去対象スロット（`center` または `right`、あるいは `all`）。指定されたスロットのスタックから一時コンテンツを Pop し、非表示（デフォルトコンテンツへの復帰）にする。
   - **入れ替えイベント (`type: "swap_slots"`)**:
     - `center` と `right` のスロット表示スタックを丸ごと（または最前面のコンテンツ同士を）左右で入れ替える。

3. **差分更新によるチラつき防止**
   - ダッシュボードのデータ更新時において、各表示コンポーネントは状態（URLやコンテンツ種別など）に変更がない限り、DOMの再生成（`innerHTML` の書き換えや iframe の再読み込みなど）を行わない。
   - 地図（`map.html`）とナビ地図（`nav.html`）は親画面からリロードされず、iframe内部で2秒ごとに `/api/location` を呼び出す自律的な現在地部分更新を維持する。

4. **現在地取得API (`GET /api/location`)**
   - 既定ではローカルのgpsdを優先し、使えない場合だけGPSデバイスから短時間だけNMEAを読み、現在地をJSONで返す。
   - `ARGOS_LOCATION_PROVIDER=remote` の場合は `ARGOS_REMOTE_LOCATION_URL` から現在地JSONを取得する。
   - リモート現在地JSONは、緯度経度を直下に持つ形式と、`point` オブジェクト配下に持つ形式を扱える。
   - `ARGOS_REMOTE_LOCATION_TIMEOUT_SECONDS` でリモート現在地APIのタイムアウト秒数を指定する。
   - 地図で `follow=1` を指定した場合、`map.html` はこのAPIを2秒ごとに呼び出して現在地マーカーを更新し、ズームレベルが変わる必要がある場合や現在地が画面外にはみ出そうになった時、または目的地の接近や離脱（最接近地点から一定距離離れて目的地ピンが削除された時）による表示の切り替え時のみ、表示範囲を自動調整する。接近しきい値 `near_threshold` や離脱しきい値 `far_offset` をクエリパラメータでカスタマイズできる。
   - ナビ地図（`nav.html`）はこのAPIを2秒ごとに呼び出し、現在地を常に画面中央へ追従させる。`orientation=north` では北を上に固定し、`orientation=heading` では取得できた進行方向を上にする。進行方向が取得できない場合は北上表示に近い挙動へフォールバックする。

ダッシュボードは `/static/*` へのGETリクエストを受け取った際、パッケージ内の `static` ディレクトリに配備された静的アセットを適切なMIMEタイプで返す。
- `map.html` (地図): Leaflet.js を使用して、クエリパラメータで指定された座標をダークモード風の地図へ表示する。現在地は青い円形マーカー、目的地は赤いピンで表示する。地図の左下には距離の凡例（メートル法のスケールバー）を常時表示する。`points`（4番目のパラメータ）または `color` パラメータで各マーカーに色（例: `#ff9900` などのカラーコードやカラー名）を指定でき、目的地と経由地を異なる色で表示できる。`label_mode=permanent|hover|popup` でラベルを常時表示、ホバー時表示、タップ時のみ表示から選べる。`follow=1` が有効な場合は現在地に追従し、通常時は全マーカーを収める広域表示とし、特定の目的地に接近しきい値（`near_threshold`、デフォルト2km）以内に近づいた時はその目的地と現在地の拡大表示（ズームイン）にし、最接近地点から離脱しきい値（`far_offset`、デフォルト500m）以上離れた時は、その目的地ピンを地図から自動削除した上で、再び全体表示（ズームアウト）に自動調整する。
- `nav.html` (ナビ地図): Leaflet.js を使用して、現在地を中心へ追従するカーナビ風の地図を表示する。`zoom`、`orientation=north|heading`、`interval` をクエリパラメータで指定できる。通常地図とは別の一時コンテンツとして扱い、中央スロットに `nav.html`、右スロットに `map.html` を同時表示できる。スキルからのナビ表示は `replace_top=true` を指定し、ズーム変更のたびに閉じる回数が増えないよう最前面を差し替える。
- `reader.html` (Markdown): marked.js を使用し、`postMessage` やクエリで受け取ったMarkdownテキストを綺麗にレンダリングする。
- `viewer.html` (画像): クエリで指定された画像URLをアスペクト比維持で表示し、ズームイン・アウト・リセット機能を提供する。

`scripts/show-ttyd-tmux-overlay.py` は `tmux` セッションを作成し、`ttyd` で `tmux attach-session` を `127.0.0.1` に公開してから、`overlay_type="terminal"` の表示イベントをダッシュボードへ送る。表示自体は既存の iframe overlay を使う。ttyd はブラウザからシェルを操作できるため、既定はローカルホスト公開とし、LANやインターネットへ直接公開しない。

ダッシュボード各スロットの「閉じる」ボタンをクリックすると、フロントエンド側から自動的に `clear_overlay` イベントが送信され、そのスロットがクリアされる。

#### PiSugar モバイル端末（Terminal API）

Raspberry Pi Zero 2 W に PiSugar HAT（LCD・PTTボタン・スピーカー）を載せた `argos-terminal`（別リポジトリ）を、ARGOS母艦の「遠隔ヘッド」として使うためのAPI。母艦の目の前にいなくても、Tailscale等のVPN越しにPTTで話しかけ、応答テキストと音声を端末で受け取る。端末側はSTT・エージェント・TTSを一切持たず、録音WAVの送信・テキスト表示・音声再生・スロット切替だけを担う薄いクライアントとする。

端末からの操作もダッシュボードのリモート操作として扱い、発話・応答・スロット変更はすべて `DashboardState` に記録して既存の `/api/stream` でブラウザにもライブ表示・履歴として残す。これにより後から追跡できる。端末APIはHDMIダッシュボードと同じHTTPサーバー（`ARGOS_DASHBOARD_PORT`、既定8765）上に同居し、認証は同じ `ARGOS_DASHBOARD_TOKEN` を使う。遠隔利用時は `ARGOS_DASHBOARD_HOST` をTailscaleアドレスや `0.0.0.0` へ広げて到達可能にする。端末専用の認証スコープ分離は今後の拡張点とする。

端末APIは以下のエンドポイントを提供する。いずれも `ARGOS_DASHBOARD_TOKEN` によるBearer認証を必須とする。

- `POST /api/terminal/turn`：入力を母艦のエージェントへ渡し、そのターンの結果を **Server-Sent Events** でストリーム返却する。`Content-Type: audio/wav` はSTT→エージェント→TTS、`audio/ogg`（または `audio/opus`）はWAVへデコード後に同じ処理を行う。`Content-Type: text/plain; charset=utf-8` はSTTを省略して本文を直接エージェントへ渡す。`Accept: text/event-stream` を指定するとTTSを省略して文字イベントだけを返す。Accept未指定または `*/*` は後方互換のため音声イベントも返す。受信サイズ上限は `ARGOS_DASHBOARD_UPLOAD_MAX_BYTES` を流用する。未対応の入力形式は415、Opusのデコード失敗は400を返す。イベント種別は次のとおり。
  - `transcript`：STTの文字起こし結果。`{"text": "..."}`。空文字（認識失敗）の場合は `error` を返してターンを終える。
  - `text`：エージェント応答の差分。`{"delta": "..."}`。端末はLCDへ逐次追記する。
  - `audio`：応答を句読点で分割し文単位でTTS合成したWAV。`{"seq": 0, "format": "wav", "data": "<base64>"}`。端末は `seq` 順にキュー再生する。テキスト差分が先行し音声が遅れて届くため、LCD表示が音声より先行する。リクエストヘッダー `X-Argos-Audio: opus` を付けると、各チャンクをOgg Opusへ変換して `{"format": "opus", ...}` で返す（変換失敗時はWAVのまま返すフォールバック付き）。
  - `done`：ターン完了。`{"text": "<応答全文>"}`。
  - `error`：処理失敗。`{"message": "..."}`。スロットが処理中（`RunnerSlotBusyError`）の場合もこの種別で通知する。
- `GET /api/terminal/slots`：エージェントスロット一覧と現在スロットを返す。`{"slots": [{"name": ..., "provider": ..., "active": bool}], "current": {"name": ..., "provider": ...}}`。
- `POST /api/terminal/slots/next`：次のエージェントスロットへ巡回切替し、切替後の現在スロットを返す。端末のPTTダブルクリックに対応する。
- `POST /api/terminal/slots/select`：`{"name": "...", "provider": "..."}` で指定したスロットへ切り替え、切替後の現在スロットを返す。ダッシュボード左側のスロットボタンから利用する。母艦のスロット状態を共有するため、切替はダッシュボードや目の前の端末にも一貫して反映される。

端末ターンはローカル録音と同じ本人確認ゲートを通す。母艦がロック中（本人確認が有効かつ未認証）の場合、端末の発話はLLMエージェントへ渡さず本人確認用として扱う。音声キーワードが一致すれば母艦と共有の認証状態を解除し、`text` で「本人確認しました。」を返して `done` で終える。解除できなければ `error` で本人確認を促す。顔認証は母艦のカメラに依存するため、遠隔端末からの実質的な解除手段は音声キーワードになる。認証状態は母艦と端末で共有するため、どちらで解除しても両方が解除される。本人確認が無効な場合はこのゲートを素通りする。

母艦は端末ターンを現在スロットのエージェントセッションで実行するため、目の前の端末・ダッシュボード・PiZero端末は同じ会話コンテキストを共有する。端末ターンの合成音声は母艦のスピーカーでは再生せず、SSEの `audio` イベントとして端末へ返す（`text` はブラウザ用に `DashboardState` にも積む）。処理の進行に合わせて母艦ダッシュボードの状態枠も更新する（文字起こし中=`transcribing`→考え中=`thinking`→読み上げ中=`speaking`、完了で待機へ戻す）。端末APIは `ARGOS_DASHBOARD_ENABLED=true` のときだけ有効になる。

ダッシュボードの会話欄下部からテキストを送信できる。ブラウザは `text/plain` と `Accept: text/event-stream` を使うため、STTとTTSを実行せず、同じスロット・同じ会話コンテキストへ文字で質問し文字で回答を表示する。
SPダッシュボードでは入力欄横のマイクボタンをタップして録音を開始し、再度タップして停止・送信できる。Safariの `audio/mp4`、Chrome系の `audio/webm`、Opus音声は母艦でffmpegを使ってWAVへ変換し、既存のSTTへ渡す。応答は端末ターンAPIのSSEからWAVチャンクを受信し、Web Audio APIで順次再生する。マイク許可と録音にはHTTPSのセキュアコンテキストを必要とする。
テキスト送信中の操作制限はスロット単位で管理する。処理中に別の空きスロットへ切り替えた場合、そのスロットでは続けてテキストを入力・送信できる。元スロットの回答が裏で完了した場合は未読にしてスロット表示を強調し、完了通知を追加する。スロット切替時は旧スロットの一過性状態を無効化し、選択先が空いていれば待機表示へ戻す。
送信が母艦へ届かなかった場合は、入力欄の上に理由を表示する。通信自体に失敗したときは「母艦に繋がらないみたい」と表示し、母艦がエラーを返したときは応答の `error` 本文をそのまま見せる。表示は次の送信まで残し、入力した本文は失われないよう入力欄へ戻して再送できるようにする。母艦へ届いた後の進行状況とエラーは、音声ターンと同じく状態枠と通知欄へ反映されるため、リモートで開いている別のダッシュボードからも追跡できる。この画面表示は、端末（`argos-terminal`）が送信失敗時にLCDへ「母艦に繋がらないみたい」を出しLEDをエラー色にするのと同じ役割を担う。

Codex CLI が最終回答前に `item.completed`（`agent_message`）イベントを出す場合、`ARGOS_CODEX_STREAM_MODE=stream`（既定）であればARGOSはその差分を順次処理する。途中イベントを一切取得できない場合や `final` モードの場合は、完了後の出力を句読点単位で分割して読み上げる。

エージェント呼び出し直後は、ARGOS が短い進捗メッセージを読み上げる（provider共通）。`ARGOS_ACKNOWLEDGEMENT_URL` が設定されている場合は、ユーザーの発話テキストをそのURLへ `POST /select` 送信し、返答された進捗メッセージを読み上げる（認証は `ARGOS_ACKNOWLEDGEMENT_TOKEN` を使用）。設定がない場合やエラー時は、候補からランダムに選ぶ。応答本文が届く前に待機時間が長くなった場合は、`ARGOS_AGENT_PROGRESS_FIRST_DELAY_SECONDS` 後から `ARGOS_AGENT_PROGRESS_INTERVAL_SECONDS` 間隔で追加の待機メッセージを読み上げる。進捗音声の有効/無効は `ARGOS_AGENT_PROGRESS_VOICE` で切り替える。読み上げるフレーズは `ARGOS_AGENT_PROGRESS_START_PHRASES` と `ARGOS_AGENT_PROGRESS_WAIT_PHRASES`（セミコロン/改行区切り）で上書きでき、未設定なら組み込みの既定フレーズを使う。これらの設定は旧 `ARGOS_CODEX_PROGRESS_*` 名も後方互換で読み込む（新名が優先）。メッセージはAI名を出さず、「確認するね」や「もう少し待ってね」のように音声で聞きやすい短い言い方を複数候補からランダムに選ぶ。応答本文の差分が届いた時点で進捗メッセージは停止し、進捗メッセージの再生完了を待ってから通常の応答読み上げに切り替える。

### 発話時の挨拶

ARGOS は最初の発話処理時と正常終了時に最終利用時刻を `ARGOS_GREETING_STATE_PATH` のJSONへ保存する。発話処理時は前回利用時刻と現在時刻から挨拶を選ぶ。起動しただけでは挨拶しない。

- 前回利用から10分未満: 挨拶なし
- 同日で10分以上3時間未満: `おかえり。`
- 同日で3時間以上: `久しぶり。お疲れさま。`
- 初回または日付変更後: 時間帯に応じて `おはよう。`、`こんにちは。`、`こんばんは。`

### 本人確認

`ARGOS_AUTH_ENABLED=true` の場合、ARGOS は本人確認が済むまで発話をCodexへ送らない。ロック中の発話は本人確認だけに使い、顔認証・音声キーワードのどちらで解除できても、その発話自体はCodexへ送らない（用件は解除後に改めて話す）。`ARGOS_AUTH_KEYWORD_HASH` と一致した場合にロックを解除する。Codexへ発話を送るのは、その発話の開始時点で既に認証済みだった場合だけとする。

音声キーワードはPBKDF2ハッシュとして保存する。ハッシュ作成は `uv run scripts/hash-auth-keyword.py` を使う。`ARGOS_AUTH_KEYWORD_HASH` はセミコロン、カンマ、改行区切りの複数ハッシュを受け付け、STTが `唐揚げ` を `からあげ` のように表記ゆれさせる場合も、許可したい表記のハッシュを追加できる。認証済みの有効期限は `ARGOS_AUTH_TRUST_SECONDS` で指定し、既定は30分とする。待機中に有効期限が切れた場合は、HDMIダッシュボードの状態表示を自動で `ロック中` へ戻す。連続失敗が `ARGOS_AUTH_FAILURE_THRESHOLD` に達した場合は警戒通知を出す。

`ARGOS_AUTH_FACE_ENABLED=true` の場合、起動時とロック中の発話時にカメラ照合を試す。照合に成功した場合は音声キーワード解除と同じく認証済み状態へ遷移する。ただしその発話は本人確認のためのものとみなし、Codexへは送らない（用件は解除後に改めて話す）。照合に失敗した場合は、同じ発話を音声キーワードとして検証する。ロック中の発話が空文字（PTT短押しなど）だった場合は、顔認証だけを試し、音声キーワード照合は行わない（無言で失敗回数を増やさない）。

顔検出確認は `uv run scripts/check-face-detection.py` で行う。撮影画像は `/tmp/argos/camera-latest.jpg` にもコピーする。顔サンプル登録は `uv run scripts/enroll-face-auth.py --count 5` で行う。撮影は `ARGOS_AUTH_FACE_CAPTURE_COMMAND` を使い、登録サンプルは `ARGOS_AUTH_FACE_SAMPLES_DIR` に保存する。登録時は顔が1つだけ検出された画像から、顔領域だけの指紋を保存する。顔検出にはOpenCVを使う。インストーラーはARGOS本体を `uv sync --extra face` で同期し、OpenCVを標準導入する。OpenCVが未導入、顔が検出できない、または複数の顔が検出された場合は顔認証を失敗扱いにして音声キーワードへフォールバックする。現段階の顔照合はローカル顔画像指紋の簡易比較で、しきい値は `ARGOS_AUTH_FACE_THRESHOLD`、必要一致数は `ARGOS_AUTH_FACE_MIN_MATCHES` で調整する。

顔認証に失敗し、撮影画像が残っている場合は、画像を `/tmp/argos/camera-latest.jpg` へコピーし、ダッシュボードの通知に `/camera/latest.jpg` として表示する。

`ARGOS_AUTH_FACE_DETECTOR_MODEL_PATH` と `ARGOS_AUTH_FACE_RECOGNIZER_MODEL_PATH` の両方が存在する場合は、OpenCV YuNet で顔検出し、SFace の128次元特徴量で照合する。モデルは `uv run scripts/download-face-models.py` で `~/.local/share/argos/face-models/` に取得する。SFace照合はコサイン類似度を使い、しきい値は `ARGOS_AUTH_FACE_SFACE_THRESHOLD` で指定する。モデルがない場合は従来の明暗指紋方式へフォールバックする。
撮影画像の向きは `ARGOS_AUTH_FACE_IMAGE_ROTATION` で補正する。指定できる値は `0`、`90`、`180`、`270` とする。

起動後に未認証の場合は、まず「本人確認をしてください。」と案内する。`ARGOS_AUTH_WARNING_DELAY_SECONDS` の秒数が過ぎても未認証なら、警告音と本人確認案内を `ARGOS_AUTH_WARNING_INTERVAL_SECONDS` 間隔で繰り返す。`ARGOS_AUTH_ALERT_DELAY_SECONDS` を超えたら、ダッシュボード状態を `alert`、表示名を `警戒中` にして「警戒モードに入りました。本人確認してください。」と案内する。本人確認に成功したら警告音タイマーを停止する。本人確認の連続失敗がしきい値に達した場合も同じく警戒状態へ切り替える。
PTT録音中は本人確認の繰り返し案内と警告音を再生しない。これにより、案内音声がマイクへ回り込んで音声キーワード認識を妨げることを避ける。ただし、本人確認でロック中の状態でPTTボタンが押された（録音開始した）場合は、認証実績の有無にかかわらず、ボタンを押した瞬間に非同期で短い警告チャイム（警告音）を再生し、ユーザーにロック中であることを即座に通知する。

本人確認の連続失敗がしきい値に達した場合は、ダッシュボードへ警戒通知を出し、`ARGOS_AUTH_ALERT_COMMAND` が設定されていれば外部コマンドを実行する。コマンドには `{source}`、`{message}`、`{image_path}` を埋め込める。Slack、SMS、電話などはこのコマンド先のスクリプトで実装する。

## 状態遷移

- `IDLE`: 待機
- `LISTENING`: PTT 押下中、録音中
- `BUSY`: STT、Codex、TTS の処理中

短押し1回は録音を破棄する。短押し2回は録音として扱わず、Codex スロット切替に使う。ただし本人確認でロック中の場合は、短いキーワード発話を取りこぼさないよう、短押しでも録音として扱う。この場合は短押しキャンセルやダブルクリック切替より本人確認録音を優先する。

`BUSY` 中にPTTを押した場合は、再生中の音声をキャンセルしてすぐ `LISTENING` に遷移する。短押しで離した場合は録音を破棄し、TTSキャンセルだけの操作として扱う。TTSキャンセル後も実行中エージェントの応答取得とダッシュボードへの保存は継続するため、スロット切替で裏に回った応答も未読として残る。ユーザがそのまま押し続けると録音を継続し、PTT解放時に通常どおり文字起こしとCodex実行へ進む。

前回のSTT、Codex、TTS処理が終了したときは、状態がまだ `BUSY` の場合だけ `IDLE` に戻す。処理終了と同じタイミングで次のPTT録音が始まって `LISTENING` になっている場合は、その状態を維持して解放イベントで録音を停止できるようにする。

録音WAVは `/tmp/argos/utterance-*.wav` のユニークな一時ファイル名で作成する。固定名を使わないことで、前回録音のSTT処理中に次の録音が始まっても、次の録音開始処理が前回録音ファイルを削除しないようにする。STTゲートウェイへ送るmultipartファイル名も実際の録音ファイル名に合わせる。録音をキャンセルした場合はその録音ファイルを削除し、STT処理に渡した録音ファイルも処理終了時に削除する。異常終了などで残った古い録音一時ファイルはARGOS起動時に削除する。
短い本人確認キーワードはSTT側で空文字になりやすいため、録音停止後にWAVヘッダーを修復し、前後へ短い無音を追加してから文字起こしへ渡す。

`ARGOS_PTT_GPIO` が空欄の場合、GPIO PTT入力は初期化しない。UbuntuなどGPIOがない環境ではこの設定にして起動できるようにする。値がある場合、GPIO入力は gpiozero のコールバックに処理を直接ぶら下げず、ポーリングした押下/解放エッジをキューに積み、別スレッドで順番にアプリへ渡す。これにより、録音開始やキャンセル処理中でも物理解放イベントを取り逃がしにくくする。

GPIO入力は起動直後の本人確認案内を読み上げる前に初期化する。これにより「本人確認してください」の読み上げ中にPTTを押した場合も、読み上げを止めて録音を開始できる。

## systemd ユニット

`systemd/argos.service` は Raspberry Pi 上で ARGOS を常駐させるための配布用ユニットテンプレートである。`scripts/install-systemd-services.sh` が `@PROJECT_DIR@`、`@ARGOS_USER@`、`@ARGOS_GROUP@`、`@USER_HOME@` を置換して `/etc/systemd/system` へ配置する。既定の本番配置先は `/opt/argos` とする。開発環境では `ARGOS_PROJECT_DIR=/home/<user>/argos` のように指定して同じテンプレートを使う。

- `User`、`Group` は `ARGOS_SERVICE_USER` と `ARGOS_SERVICE_GROUP` で指定し、未指定時は `argos` とする
- systemdユニットを有効化する前に、指定したサービスユーザーとグループをOS側に作成する
- `WorkingDirectory=/opt/argos` を本番の既定作業ディレクトリにする
- `EnvironmentFile=/opt/argos/.env` から設定を読み込む
- `ExecStart=/opt/argos/.venv/bin/argos` でプロジェクトの仮想環境内コマンドを起動する
- `PATH` にサービスユーザーの `.local/bin` と `.cargo/bin` を含め、Codex CLI を解決できるようにする
- `network-online.target`、`tailscale-online.target`、`sound.target` の後に起動する
- ホスト固有のautosshなどへ依存する場合は、ソースのユニットではなく実機側のsystemd drop-inで `After` と `Wants` を追加する
- 異常終了時は `Restart=on-failure` で再起動する

`systemd/argos-agent-runner.service` は Agent Runner をARGOS本体とは別に常駐させるための配布用ユニットテンプレートである。

- `User`、`Group`、`WorkingDirectory`、`EnvironmentFile` は ARGOS本体のユニットと同じ置換値を使う
- `ExecStart=/opt/argos/.venv/bin/argos-agent-runner` でRunnerを起動する
- 異常終了時は `Restart=on-failure` で再起動する

Runnerを使う場合は、`argos-agent-runner.service` を有効化したうえで、ARGOS本体側に `ARGOS_AGENT_RUNNER_URL=http://127.0.0.1:28765` と `ARGOS_AGENT_RUNNER_TOKEN` を設定する。Runnerを使わない場合、ARGOS本体は従来どおり直接エージェントCLIを起動する。

`ARGOS_AGENT_RUNNER_TOKEN` が空の場合、Runner APIは認証なしで全リクエストを受け付けてしまうため、インストーラーは `--apply` または `--update` 実行時に空ならランダムなトークンを自動生成する。ARGOS本体とRunnerは同じ `.env` を読むため、生成されたトークンは自動的に両者で共有される。Bearer認証の比較は `hmac.compare_digest` による定数時間比較で行う。また、Runner APIのリクエスト本文は1MiBを上限とし、サイズ超過や不正JSONは400で拒否する。

実運用前に `uv sync` で `.venv/bin/argos` を作成し、`.env` を実機向けに設定する。GPIO や音声デバイスへのアクセスで権限エラーが出る場合は、サービスユーザーを Raspberry Pi 側の `gpio` や `audio` グループに追加してから再ログインする。

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
ウェイクワード有効時はARGOSがマイク入力を1本だけ開き、PTT録音とウェイクワード監視へ内部配信する。`dsnoop` が実機側で48kHzを返す場合は、`ARGOS_WAKEWORD_CAPTURE_SAMPLE_RATE=48000` を指定し、ARGOS内部で16kHzへ変換する。

## ウェイクワード

ウェイクワードは `ARGOS_WAKEWORD_ENABLED=true` の場合だけ有効になる。既定では無効で、PTT操作には影響しない。

- `ARGOS_WAKEWORD_MODEL_DIR` にLiveKit形式の `argos.onnx`、`melspectrogram.onnx`、`embedding_model.onnx` を置く
- `ARGOS_WAKEWORD_EMBEDDING_HEF`が指定されている場合は、音声埋め込みモデルをHailoRT経由でHailo-8へオフロードする。メル特徴量生成と最終分類はONNX Runtimeを継続し、HEF設定が空なら全段をCPUで実行する
- しきい値は `ARGOS_WAKEWORD_THRESHOLD` を最優先に使う。`argos_eval.json` の `optimal_threshold` や `threshold` は学習時の参考値で、常時監視では自動採用しない
- 入力デバイスは通常録音と同じ `AUDIO_INPUT_DEVICES` または `AUDIO_DEVICE` を使う
- `ARGOS_WAKEWORD_CAPTURE_SAMPLE_RATE` でraw入力の実サンプルレートを指定できる。`dsnoop` が16kHz指定でも48kHzを返す環境では `48000` を指定し、ARGOS内部で16kHzへ変換してモデルとVADへ渡す
- ウェイクワード有効時は共有マイク入力を使い、PTT録音とウェイクワード監視が別々に `arecord` を起動しない
- 推論窓は無音で前詰めして、起動直後でも `ARGOS_WAKEWORD_MIN_ACTUAL_SECONDS` 秒ぶんの実音声が入った時点から判定する
- 検知後は同じ音声ストリームから発話をWAV化し、既存のSTT、本人確認、エージェント処理へ渡す
- ウェイクワード経由のSTT結果は、先頭に混ざった `アルゴス`、`アルコス`、`argos` などの呼びかけだけを除去してから本人確認と通常会話へ渡す。文中の同語は削除しない。呼びかけの表記ゆれは `ARGOS_WAKEWORD_ALIASES`（カンマ/全角読点/改行区切り）で上書きでき、別名のアシスタントにも転用できる。未設定なら組み込みの既定（アルゴス等）を使う
- ウェイクワードは `ready` または `locked` の時だけ受け付ける。考え中、文字起こし中、録音中、読み上げ中の検知は処理中タスクやTTSをキャンセルせず無視する
- ウェイクワード検知後の録音中にPTTを押した場合は、別録音を開始せず、そのウェイクワード発話をPTT解放まで継続する
- 読み上げ中は自己音声による誤検知を避けるため、ウェイクワード検知を無視する
- 読み上げ終了後も `ARGOS_WAKEWORD_TTS_COOLDOWN_SECONDS` 秒間はウェイクワード検知を無視し、検知バッファをクリアする。これはウェイクワードだけの自己音声対策で、PTT録音には影響しない
- バージイン: `ARGOS_WAKEWORD_BARGEIN_ENABLED=true` の場合、読み上げ中（`speaking`）でもウェイクワードで割り込みを許し、進行中のTTSをキャンセルして録音へ切り替える。追いかけ受付とTTS直後クールダウンの制約もバージイン時は無視する。PTTのボタン割り込みに相当する手段をウェイクワードonly運用に与えるための機能。既定は無効
  - 前提: 自己音声（スピーカー→マイクのエコー）で自分の「アルゴス」に反応してしまうため、マイク入力を音響エコーキャンセル(AEC)済みの仮想ソースに差し替えることが必須。実機検証ではPipeWire `module-echo-cancel`（WebRTC AEC3）でエコーを閾値下(約0.05)まで抑制でき、ユーザ発話（ダブルトーク）は残ることを確認済み。線形AEC（speexdsp）は不十分
  - 二重防御: AECで消し切れない残響対策として、ARGOS自身がウェイクワードを含むチャンクを読み上げている最中（`SpeechController.is_speaking_wakeword()`）はバージインを抑止する
  - ALSA直の橋渡し: ARGOSはTTSを`aplay`、ウェイクワード/STTを`arecord`で鳴らし、どちらもALSAを直接叩く（PipeWireを経由しない）。PipeWireのノード名`ec-source`/`ec-sink`（ハイフン）はそのままでは`aplay`/`arecord`が開けないため、ALSA↔PipeWireを橋渡しするALSA PCM`ec_source`/`ec_sink`（アンダースコア、`type pipewire`）を`/etc/asound.conf`に定義し、`.env`にはこの**ALSA名**を指定する。橋渡しには`pipewire-alsa`パッケージが要る。（`~/.asoundrc`はRaspberry Pi OS等で再起動時に消えることがあるため、システムの`/etc/asound.conf`に置く）
  - 音切れ対策: エコーキャンセルを挟むとHDMI出力で2種のプチプチが出る。(a) 無音時にHDMIがアイドルでサスペンド↔再開を繰り返す音、(b) Pi 5でDMA headroomが小さく再生が間に合わずxrunする音。対策としてWirePlumberのドロップインで`alsa_output`に`session.suspend-timeout-seconds = 0`（サスペンド無効化）と`api.alsa.headroom = 8192`（DMAバッファ余裕）を設定する
  - 音量: ARGOSのTTSは従来`aplay`でHDMIへ直出し＝PipeWire音量をバイパスして大音量だったが、`ec_sink`経由にするとTTS音量がPipeWireの既定sink音量に従う。そのため既定sink音量を100%に固定し、音量調整はARGOS内部のvolume設定で行う
  - 導入手順: `scripts/setup-echo-cancel.sh` が (1) PipeWire永続ドロップイン（既定入出力に追従する`ec-source`/`ec-sink`ノード）、(2) `/etc/asound.conf`のブリッジPCM`ec_source`/`ec_sink`（sudo要）、(3) WirePlumberドロップイン（サスペンド無効化＋HDMI headroom増）、(4) 既定sink音量100%固定 を書き込む。`default`は変更しないため通常運用には影響しない。依存が不足していれば案内して中断し、`--install-deps`で`pipewire-alsa`と`libspa-0.2-modules`をapt導入できる。反映（`systemctl --user restart pipewire pipewire-pulse wireplumber`）後は`aplay -D ec_sink ...`で疎通確認してから、`.env`に`AUDIO_OUTPUT_DEVICE=ec_sink`/`AUDIO_INPUT_DEVICES=ec_source`/`ARGOS_WAKEWORD_BARGEIN_ENABLED=true`を設定する。デバイス名は環境で変わるためPipeWireの既定デバイスに紐付けて移植性を確保しており、Raspberry Pi OS / Ubuntu 共通。これらの設定はファイルとして永続するため再起動後も有効。車載やデスクトップUbuntuなど環境が変わる場合は同条件でエコー抑制を再計測する
  - 元に戻す: `scripts/setup-echo-cancel.sh --revert` でPipeWireドロップイン、`/etc/asound.conf`のARGOSブロック、WirePlumberドロップインを削除する（`default`や既存設定、`.env`、既定sink音量には触れない）。反映は `systemctl --user restart pipewire pipewire-pulse wireplumber`。`.env`側で入出力デバイスや`ARGOS_WAKEWORD_BARGEIN_ENABLED`を変更していた場合はそちらも戻す
- 応答の読み上げが終わったら、`ARGOS_WAKEWORD_FOLLOWUP_SECONDS`（既定3秒、0で無効）だけ「追いかけ受付窓」を開き、ウェイクワードを言い直さなくても続けて話せるようにする。窓の中で発話（RMSが `SILENCE_RMS_THRESHOLD` 以上）を検知したら、ウェイクワード無しでそのまま録音・処理する。追いかけ受付の録音は呼びかけを含まないため、STTの呼びかけ必須判定（`ARGOS_WAKEWORD_REQUIRE_STT_WAKEWORD`）と先頭呼びかけ除去はスキップする
- 追いかけ受付は本人確認済みのときだけ開く。ロック中は従来どおりウェイクワードと本人確認を求める。窓を開いている間はダッシュボード状態を `followup`（継続受付中）にして画面を起こしたままにし、無音のまま窓が締め切られたら待機表示へ戻す。窓の中で発話に応答したら、その応答のあとに再び窓を開き、会話が続く限り連続で受け付ける。PTT押下時は窓を閉じる。自己音声対策のクールダウンは追いかけ受付には適用しない
- `ARGOS_WAKEWORD_REQUIRE_STT_WAKEWORD=true` の場合、ウェイクワード後録音のSTT結果が「アルゴス」などの呼びかけから始まる時だけ本文処理へ進む。自宅など通信遅延が小さく誤検知を強く抑えたい環境向けの設定とする
- 通常のウェイクワード録音が、STT結果なし、呼びかけなし、呼びかけのみのいずれかで破棄された場合、`ARGOS_WAKEWORD_FALSE_POSITIVE_CAPTURE=true` なら録音と判定情報JSONを `ARGOS_WAKEWORD_FALSE_POSITIVE_DIR/hard_negative/` へ保存する。既定保存先はtmpfs上の `/tmp/argos/wakeword-candidates` とし、追いかけ受付、本人確認ロック中の無言録音、正常に処理した発話は保存しない
- 保存音声は誤検知の候補であり、自動では学習データへ追加しない。内容を確認して本当に誤検知したものだけwakeword trainerの `raw-dataset/hard_negative/` へ移し、正しい呼びかけや私的な会話を誤って負例にしない
- 短い発話の先頭を取りこぼさないよう、`ARGOS_WAKEWORD_PRE_ROLL_SECONDS` 秒ぶんの検知直前音声もWAV先頭へ含める
- 発話録音は既定でSilero VADを使う。`ARGOS_WAKEWORD_ENDPOINT_MODE=vad` の場合、`ARGOS_WAKEWORD_VAD_THRESHOLD` 以上を発話、`ARGOS_WAKEWORD_VAD_MIN_SILENCE_SECONDS` 継続を終了候補として扱う
- `ARGOS_WAKEWORD_ENDPOINT_MODE=rms` の場合、`ARGOS_WAKEWORD_RECORD_MIN_SECONDS` 以上録音した後、`SILENCE_RMS_THRESHOLD` 未満の状態が `ARGOS_WAKEWORD_RECORD_SILENCE_SECONDS` 続いたら終了する
- 最大録音秒数は `ARGOS_WAKEWORD_RECORD_MAX_SECONDS` で制限する
- `ARGOS_WAKEWORD_SCORE_LOG_PATH` を指定すると、調査用に1秒ごとの最大スコアと検知有無を指定ファイルへ追記する。SDカード摩耗を避ける場合は `/tmp/argos/wakeword-score.log` のようにtmpfs配下を指定する

ウェイクワード無効時は従来どおり、PTT録音の開始時だけ `arecord` を起動する。

PTT録音とウェイクワード後録音は、RMS音量だけでは破棄しない。車内では無発話時と小声発話時のRMS差が小さいため、録音後はSTTへ渡し、文字起こし結果が空の場合だけ通知する。ただし本人確認でロック中に文字起こしが空になった場合（PTT短押しなど）は通知せず、顔認証だけを試してロック解除を試みる。

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
名前,provider,cwd[,voicevox_speaker[,model]]
```

- `名前`: 読み上げるスロット名
- `provider`: `codex` などのエージェント種別
- `cwd`: エージェントの作業ディレクトリ
- `voicevox_speaker`: 任意。指定した場合、このスロットの読み上げだけ指定VOICEVOX話者IDを使う
- `model`: 任意。指定した場合、このスロットのCLI起動時にモデルを指定する。話者を省略してモデルだけ指定する場合は4項目目を空にする

3項目の旧形式とVOICEVOX話者までの4項目形式はそのまま読み込む。モデル未指定時は、Codexでは既存の `ARGOS_CODEX_MODEL`、Claudeでは `ARGOS_CLAUDE_MODEL`、Antigravityでは `ARGOS_ANTIGRAVITY_MODEL`、Hermesでは既存の `ARGOS_HERMES_MODEL` を使い、それも空なら各CLIの既定モデルに任せる。

スロットを指定しない場合は、`ARGOS_AGENT_SLOT_NAME`、`ARGOS_AGENT_PROVIDER`、`ARGOS_AGENT_CWD` から既定スロットを作る。既定スロットのVOICEVOX話者IDは `ARGOS_AGENT_SLOT_VOICEVOX_SPEAKER` で指定できる。旧 `ARGOS_CODEX_SLOT_N` は互換のため読み込むが、新規設定では `ARGOS_AGENT_SLOT_N` を使う。

新規インストール時の `argos-install --configure` は、利用するproviderを `codex`、`antigravity`、`claude`、`hermes` から選ばせ、選択したproviderごとにスロット名、作業ディレクトリ、VOICEVOX話者、モデルを確認して `ARGOS_AGENT_SLOT_N` を生成する。空入力の場合は既存値またはprovider全体設定を維持する。

Argos が管理するセッションIDは `ARGOS_AGENT_STATE_PATH` に保存する。既定値は `~/.argos/agent-sessions.json` とする。これはCodexの設定ではなくArgos自身の状態なので、`CODEX_HOME` には保存しない。旧 `CODEX_HOME/argos-sessions.json` が存在する場合は互換のため読み込み、保存は新しい `ARGOS_AGENT_STATE_PATH` へ行う。

ARGOS は各スロットの会話開始時だけ、車載音声アシスタントとしての振る舞い、短い日本語回答、スキル配置場所などの共通システム指示をエージェントへ付与する。注入済み状態は `ARGOS_AGENT_SYSTEM_PROMPT_STATE_PATH` に保存し、同じスロットの2回目以降の発話では通常のユーザー発話だけを送る。`/reset` で現在スロットを新規会話にした場合は注入済み状態も消し、次の発話で再度システム指示を付与する。追加指示は `ARGOS_AGENT_SYSTEM_PROMPT` または `ARGOS_AGENT_SYSTEM_PROMPT_FILE` で指定でき、スキル配置場所は `ARGOS_AGENT_SKILLS_DIR` で指定する。組み込みの車載向け既定指示そのものを差し替えたい場合は `ARGOS_AGENT_DEFAULT_SYSTEM_PROMPT` に全文を設定する（別用途へ転用する際に使う。空なら既定文面を使う）。

Codex固有の `CODEX_HOME` は `ARGOS_CODEX_HOME` で全体設定として指定する。`ARGOS_CODEX_MODEL` はスロットにモデルがない場合の後方互換フォールバックとして扱う。

モデルを変更してもスロットの保存キーとprovider側セッションIDは維持し、次の発話から新しいモデルで同じ会話を再開する。モデル変更を理由に自動リセットはしない。providerが継続を拒否した場合はエラーを表示し、必要な場合だけ利用者が既存のセッションリセットを実行する。

ダッシュボードのCURRENT SLOTには `provider · model` を表示する。モデル未指定でCLI既定に任せている場合はproviderだけを表示する。

## Antigravity CLI

`provider` が `antigravity` のスロットでは、ARGOS は `agy` CLI を1発話ごとに起動する。初回は次の形で実行する。

```bash
agy --print <prompt>
```

`ARGOS_ANTIGRAVITY_CONTINUE_SESSION=true` の場合だけ、同じARGOSプロセス内の継続発話では、Antigravity の `last_conversations.json` から取得した会話IDを使い、次の形で実行する。

```bash
agy --conversation <conversation_id> --print <prompt>
```

`ARGOS_ANTIGRAVITY_COMMAND` で `agy` のパスを指定する。既定値は `agy` とし、systemd の `PATH` から解決する。Antigravity のキャッシュは `ARGOS_ANTIGRAVITY_HOME` から読み、既定値は `~/.gemini/antigravity-cli` とする。`ARGOS_ANTIGRAVITY_SKIP_PERMISSIONS=true` の場合は `--dangerously-skip-permissions` を渡す。`ARGOS_ANTIGRAVITY_SANDBOX=true` の場合は `--sandbox` を渡す。

Antigravity は会話再開時に過去の画面出力を標準出力へ混ぜることがある。ARGOS は `agy` の標準出力を回答本文としては使わず、実行後に `transcript_full.jsonl` または `transcript.jsonl` の追加分を読み、末尾から `source=MODEL`、`type=PLANNER_RESPONSE`、`status=DONE`、`content` ありのエントリーだけを回答として扱う。`agy` の標準出力と標準エラーは調査用に `/tmp/argos/antigravity-raw.log` と `/tmp/argos/antigravity-error.log` へ保存する。
transcript にUTF-8として読めないバイトが混ざっている場合でも、ARGOS は読み取りを継続し、壊れた行を無視して最新の完了済み回答を探す。

既定では毎回新規会話として起動し、`--conversation` は渡さない。会話を継続したい場合だけ `ARGOS_ANTIGRAVITY_CONTINUE_SESSION=true` を指定する。サービス再起動後も保存済み会話IDを復元したい場合は、さらに `ARGOS_ANTIGRAVITY_RESUME_SAVED=true` を指定する。`ARGOS_ANTIGRAVITY_PROMPT_PREFIX` は任意の固定prefixだが、既定では空にする。読み上げ向けの整形はprovider個別ではなく、共通のTTSフィルター側で扱う。

## Hermes Agent CLI

Hermes provider は `hermes chat -q <prompt> -Q --source <source>` を使う。`-Q` によりプログラム向けの出力にし、`ARGOS_HERMES_PASS_SESSION_ID=true` の場合は `--pass-session-id` を渡す。`ARGOS_HERMES_MODEL`、`ARGOS_HERMES_PROVIDER`、`ARGOS_HERMES_TOOLSETS`、`ARGOS_HERMES_SKILLS` はそれぞれ Hermes CLI の `--model`、`--provider`、`--toolsets`、`--skills` に対応する。追加オプションは `ARGOS_HERMES_EXTRA_ARGS` で指定する。

Hermes の session ID は `ARGOS_AGENT_STATE_PATH` にスロットごとに保存する。`ARGOS_HERMES_RESUME_SAVED=true` の場合、保存済みsession IDを起動時に復元し、次回実行では `--resume <session_id>` を渡す。`/reset` では保存済みsession IDを削除し、次回から新規会話として扱う。Hermes側の会話履歴本体は Hermes の管理領域に保存され、ARGOS はsession IDだけを保持する。
