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

tts-filter を呼び出す前に、ARGOS本体で読み上げの軽いローカル補正を通す。現時点では「5タップ」を「ごタップ」へ補正し、音声合成エンジン（VOICEVOXやKokoro TTS）が誤って「あやまたっぷ」と発音するのを防ぐ。

### VOICEVOX

VOICEVOX Engine は次の順で呼び出す。

1. `POST /audio_query?text=<text>&speaker=<speaker>`
2. `POST /synthesis?speaker=<speaker>`

`audio_query` の JSON に `outputSamplingRate` と `VOICEVOX_SPEED_SCALE` で指定した `speedScale` を設定してから `synthesis` に渡す。

`VOICEVOX_URL` が空の場合は VOICEVOX を使わず、Kokoro TTS で日本語音声を生成する。`VOICEVOX_URL` が設定済みでも、`audio_query` または `synthesis` でエラーが起きた場合はダッシュボードに `VOICEVOX` エラーを通知し、その発話を Kokoro TTS で読み上げる。

Kokoro TTS は `ARGOS_KOKORO_VOICE`、`ARGOS_KOKORO_SPEED`、`ARGOS_KOKORO_REPO_ID`、`ARGOS_KOKORO_SAMPLE_RATE` で調整する。Kokoro を使う環境では `uv sync --extra kokoro` を実行し、必要に応じて `uv run python -m unidic download` で日本語辞書を用意する。

### ST7789 LCD

`ARGOS_LCD_ENABLED=true` の場合、ARGOS は読み上げる文を ST7789 LCD にも表示する。物理解像度は既定で 76x284 とし、横向き表示になるよう描画内容を90度回転して転送する。日本語フォントは IPA Gothic、IPA P Gothic、IPAex Gothic の順に探し、`ARGOS_LCD_FONT_PATH` が指定されている場合はそれを優先する。IPA系フォントが見つからない場合、LCD表示だけを無効化する。夜間でも明るくなりすぎないよう、ST7789 の色反転を無効にして黒背景に白文字で表示する。

HDMIダッシュボードは、現在の状態、現在のエージェントスロット名、provider、会話履歴、通知を表示する。PTT短押しで録音を破棄した場合や、PTTダブルクリックでスロットを切り替えた場合は、認証状態に応じて表示を待機中またはロック中へ戻し、録音中表示を残さない。本人確認が必要なロック中にPTTを押した場合は、通常の録音中ではなく「本人確認録音中」と表示し、PTT解放後は「本人確認中」と表示する。これにより、発話が本人確認用であることと、PTT入力が認識されたことを画面上でも示す。

### LLM エージェント

ARGOS 本体は `AgentClient` インターフェース越しにLLMエージェントへ発話を送る。プロバイダーは `ARGOS_AGENT_PROVIDER` で選択し、現在の既定値は `codex` とする。`codex`、`antigravity`、`hermes` を指定できる。未対応のプロバイダーが指定された場合は起動時にエラーにする。

Codex、Antigravity、Hermes、将来の別エージェントはこの層の実装として追加する。常駐プロセスが必要なエージェントは、今後 `AgentClient` の実装内でプロセス維持や別通信方式を扱い、ARGOS 本体のSTT、TTS、認証、ダッシュボード処理からは隠蔽する。

`ARGOS_AGENT_RUNNER_URL` が設定されている場合、ARGOS 本体は Codex、Antigravity、Hermes を直接起動せず、Agent Runner HTTP APIへジョブを作成する。Runnerは `argos-agent-runner` コマンドで別プロセスとして起動し、`ARGOS_AGENT_RUNNER_HOST`、`ARGOS_AGENT_RUNNER_PORT` で待ち受ける。更新系APIは `ARGOS_AGENT_RUNNER_TOKEN` によるBearer認証に対応する。

Agent Runner はジョブごとに `ARGOS_AGENT_RUNNER_STATE_DIR/jobs/<job_id>/` を作成し、`job.json`、`prompt.txt`、`output.txt`、`result.txt`、`error.txt` を保存する。ジョブ状態は `queued`、`running`、`completed`、`delivered`、`failed`、`failed_delivered`、`cancelled` を使い、処理完了とARGOS本体への配信済み状態を分ける。これにより、ARGOS本体が再起動してもRunner側の実行結果を後から確認でき、配信済み結果を起動のたびに繰り返し読み上げる事故を避ける。

初期版のRunnerクライアントは、ジョブ完了までポーリングして最終結果をARGOS本体へ返す。ARGOS本体は起動中、Runnerの未配信完了ジョブを定期的に確認し、見つけた結果を該当スロットの会話履歴へ追加し、未読表示と通知を出してから配信済みにする。通常のTTSキャンセルやスロット切替ではRunnerジョブを停止しない。明示的なジョブキャンセルAPIは今後の拡張点とする。セッションリセットは `POST /api/slots/reset` で現在スロットの保存済みセッションIDをRunner側から削除する。

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
`ARGOS_DASHBOARD_SCREENSAVER_SECONDS` で指定した秒数だけ画面操作がない場合、ダッシュボードは全画面の黒いオーバーレイを表示する。0以下を指定すると無効化する。この段階ではバックライトやHDMI出力は消さず、タッチ、ポインター、キー、ホイール操作、PTT録音開始、または音声読み上げ開始で黒表示を解除する。
左側のブランド領域には読み上げミュートボタンを表示する。ボタンはARGOSロゴ行の右端に置き、角丸の小型ボタンとして表示する。通常時の文言は「ミュート」とし、薄いグレーで表示する。ミュート中は文言を「ミュート中」に変え、黄色の枠で強調する。操作は `POST /api/control` で受け付け、`mute`、`unmute`、`toggle_mute` をサポートする。このAPIも `ARGOS_DASHBOARD_TOKEN` によるBearer認証を必須とする。ミュートON時は再生中の音声を停止し、TTSワーカーは次のチャンク再生前に待機する。解除後はキューに残っている読み上げを再開する。音声コマンドによるミュート切替は行わない。ミュート状態はボタン表示で示し、録音中、考え中、読み上げ中などの動作ステータスは上書きしない。変更したミュート状態は `ARGOS_AUDIO_STATE_PATH` のJSONへ保存し、ARGOS再起動後に復元する。
左側パネルにはフォントサイズ切替ボタンを表示し、ダッシュボードの主要テキストを `小`、`中`、`大` から選べるようにする。選択値はキオスクブラウザのローカルストレージへ保存し、画面再読み込み後も維持する。切替対象は会話欄、通知欄、現在スロット、状態表示、スロットチップなどの可読性に関わるテキストとする。
左側パネルの `CURRENT SLOT` にはセッションリセットボタンを表示する。誤操作防止のため、1回目のタップで確認表示に切り替え、5秒以内にもう一度タップした場合だけ `POST /api/control` に `{"action":"reset_agent_session"}` を送る。この操作は現在スロットのエージェントセッションIDだけを削除し、ダッシュボードに残っている会話履歴や通知は削除しない。リセット後の次回エージェント呼び出しは新規セッションとして開始し、完了時に通常どおり新しいセッションIDを保存する。
左側パネルの左端には読み上げ音量の縦スライダーを表示する。スライダーは `POST /api/control` に `{"action":"set_volume","volume":0..100}` を送信し、ARGOS本体の `AudioPlayer` が16bit PCM WAVを小分けに再生しながらソフトウェア音量を反映する。これにより `plughw` 直指定でALSAミキサーを通らない出力でも、再生中の次の小さい再生ブロックから読み上げ音量を変更できる。ALSAミキサー操作は `AUDIO_OUTPUT_CARD` が設定されている場合はそのカード、未設定の場合はデフォルトミキサーへベストエフォートで送る。起動時は `ARGOS_AUDIO_STATE_PATH` の保存済み音量を優先し、保存済み音量がない場合だけ `AUDIO_OUTPUT_VOLUME` を初期値として使う。保存値が壊れている場合は無視する。
文字起こし、LLMエージェント、TTSフィルター、VOICEVOX、音声再生で内部エラーが起きた場合は、通知欄へ優先度 `high` の通知を追加する。直前と同一のエラーは重複追加しない。また、LLMエージェントからの応答取得でエラーが発生した際は、リミット制限エラー（`rate limit`、`quota`、`limit`など）であれば「リミット制限に達しました。」、その他の一般エラーであれば「エージェントの応答取得に失敗しました。」と音声で読み上げて報告する。

キオスク表示は `argos-dashboard-kiosk.service` をユーザーsystemdへインストールして常駐させる。Chromiumが異常終了した場合は自動再起動する。キオスク画面では管理ポリシー `TranslateEnabled=false` で翻訳UIを無効化し、ダッシュボード上のマウスカーソルを非表示にする。
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
   - 地図で `follow=1` を指定した場合、`map.html` はこのAPIを2秒ごとに呼び出し、現在地マーカーだけを更新する。
   - ナビ地図（`nav.html`）はこのAPIを2秒ごとに呼び出し、現在地を常に画面中央へ追従させる。`orientation=north` では北を上に固定し、`orientation=heading` では取得できた進行方向を上にする。進行方向が取得できない場合は北上表示に近い挙動へフォールバックする。

ダッシュボードは `/static/*` へのGETリクエストを受け取った際、パッケージ内の `static` ディレクトリに配備された静的アセットを適切なMIMEタイプで返す。
- `map.html` (地図): Leaflet.js を使用して、クエリパラメータで指定された座標をダークモード風の地図へ表示する。現在地は青い円形マーカー、目的地は赤いピンで表示する。`points`（4番目のパラメータ）または `color` パラメータで各マーカーに色（例: `#ff9900` などのカラーコードやカラー名）を指定でき、目的地と経由地を異なる色で表示できる。`label_mode=permanent|hover|popup` でラベルを常時表示、ホバー時表示、タップ時のみ表示から選べる。
- `nav.html` (ナビ地図): Leaflet.js を使用して、現在地を中心へ追従するカーナビ風の地図を表示する。`zoom`、`orientation=north|heading`、`interval` をクエリパラメータで指定できる。通常地図とは別の一時コンテンツとして扱い、中央スロットに `nav.html`、右スロットに `map.html` を同時表示できる。スキルからのナビ表示は `replace_top=true` を指定し、ズーム変更のたびに閉じる回数が増えないよう最前面を差し替える。
- `reader.html` (Markdown): marked.js を使用し、`postMessage` やクエリで受け取ったMarkdownテキストを綺麗にレンダリングする。
- `viewer.html` (画像): クエリで指定された画像URLをアスペクト比維持で表示し、ズームイン・アウト・リセット機能を提供する。

ダッシュボード各スロットの「閉じる」ボタンをクリックすると、フロントエンド側から自動的に `clear_overlay` イベントが送信され、そのスロットがクリアされる。

Codex CLI が最終回答前に途中イベントを出す場合、ARGOS はその差分を順次処理する。CLI 側が最終回答まで応答テキストを出さない場合、完全なトークンストリーミングにはならないが、最終回答の読み上げは句読点単位で分割される。

Codex 呼び出し直後は、ARGOS が短い進捗メッセージを読み上げる。`ARGOS_ACKNOWLEDGEMENT_URL` が設定されている場合は、ユーザーの発話テキストをそのURLへ `POST /select` 送信し、返答された進捗メッセージを読み上げる（認証は `ARGOS_ACKNOWLEDGEMENT_TOKEN` を使用）。設定がない場合やエラー時は、候補からランダムに選ぶ。応答本文が届く前に待機時間が長くなった場合は、`ARGOS_CODEX_PROGRESS_FIRST_DELAY_SECONDS` 後から `ARGOS_CODEX_PROGRESS_INTERVAL_SECONDS` 間隔で追加の待機メッセージを読み上げる。メッセージはAI名を出さず、「確認するね」や「もう少し待ってね」のように音声で聞きやすい短い言い方を複数候補からランダムに選ぶ。応答本文の差分が届いた時点で進捗メッセージは停止し、進捗メッセージの再生完了を待ってから通常の応答読み上げに切り替える。

### 発話時の挨拶

ARGOS は最初の発話処理時と正常終了時に最終利用時刻を `ARGOS_GREETING_STATE_PATH` のJSONへ保存する。発話処理時は前回利用時刻と現在時刻から挨拶を選ぶ。起動しただけでは挨拶しない。

- 前回利用から10分未満: 挨拶なし
- 同日で10分以上3時間未満: `おかえり。`
- 同日で3時間以上: `久しぶり。お疲れさま。`
- 初回または日付変更後: 時間帯に応じて `おはよう。`、`こんにちは。`、`こんばんは。`

### 本人確認

`ARGOS_AUTH_ENABLED=true` の場合、ARGOS は本人確認が済むまで発話をCodexへ送らない。ロック中の発話は文字起こしだけに使い、`ARGOS_AUTH_KEYWORD_HASH` と一致した場合にロックを解除する。解除キーワードそのものはCodexへ送らない。

音声キーワードはPBKDF2ハッシュとして保存する。ハッシュ作成は `uv run scripts/hash-auth-keyword.py` を使う。`ARGOS_AUTH_KEYWORD_HASH` はセミコロン、カンマ、改行区切りの複数ハッシュを受け付け、STTが `唐揚げ` を `からあげ` のように表記ゆれさせる場合も、許可したい表記のハッシュを追加できる。認証済みの有効期限は `ARGOS_AUTH_TRUST_SECONDS` で指定し、既定は30分とする。待機中に有効期限が切れた場合は、HDMIダッシュボードの状態表示を自動で `ロック中` へ戻す。連続失敗が `ARGOS_AUTH_FAILURE_THRESHOLD` に達した場合は警戒通知を出す。

`ARGOS_AUTH_FACE_ENABLED=true` の場合、起動時とロック中の発話時にカメラ照合を試す。照合に成功した場合は音声キーワード解除と同じく認証済み状態へ遷移し、その発話をCodexへ送る。照合に失敗した場合は、同じ発話を音声キーワードとして検証する。

顔検出確認は `uv run scripts/check-face-detection.py` で行う。撮影画像は `/tmp/argos/camera-latest.jpg` にもコピーする。顔サンプル登録は `uv run scripts/enroll-face-auth.py --count 5` で行う。撮影は `ARGOS_AUTH_FACE_CAPTURE_COMMAND` を使い、登録サンプルは `ARGOS_AUTH_FACE_SAMPLES_DIR` に保存する。登録時は顔が1つだけ検出された画像から、顔領域だけの指紋を保存する。顔検出にはOpenCVを使う。OpenCVが未導入、顔が検出できない、または複数の顔が検出された場合は顔認証を失敗扱いにして音声キーワードへフォールバックする。現段階の顔照合はローカル顔画像指紋の簡易比較で、しきい値は `ARGOS_AUTH_FACE_THRESHOLD`、必要一致数は `ARGOS_AUTH_FACE_MIN_MATCHES` で調整する。

顔認証に失敗し、撮影画像が残っている場合は、画像を `/tmp/argos/camera-latest.jpg` へコピーし、ダッシュボードの通知に `/camera/latest.jpg` として表示する。

`ARGOS_AUTH_FACE_DETECTOR_MODEL_PATH` と `ARGOS_AUTH_FACE_RECOGNIZER_MODEL_PATH` の両方が存在する場合は、OpenCV YuNet で顔検出し、SFace の128次元特徴量で照合する。モデルは `uv run scripts/download-face-models.py` で `~/.local/share/argos/face-models/` に取得する。SFace照合はコサイン類似度を使い、しきい値は `ARGOS_AUTH_FACE_SFACE_THRESHOLD` で指定する。モデルがない場合は従来の明暗指紋方式へフォールバックする。
撮影画像の向きは `ARGOS_AUTH_FACE_IMAGE_ROTATION` で補正する。指定できる値は `0`、`90`、`180`、`270` とする。

起動後に未認証の場合は、まず「本人確認をしてください。」と案内する。`ARGOS_AUTH_WARNING_DELAY_SECONDS` の秒数が過ぎても未認証なら、警告音と本人確認案内を `ARGOS_AUTH_WARNING_INTERVAL_SECONDS` 間隔で繰り返す。`ARGOS_AUTH_ALERT_DELAY_SECONDS` を超えたら、ダッシュボード状態を `alert`、表示名を `警戒中` にして「警戒モードに入りました。本人確認してください。」と案内する。本人確認に成功したら警告音タイマーを停止する。本人確認の連続失敗がしきい値に達した場合も同じく警戒状態へ切り替える。
PTT録音中は本人確認の繰り返し案内と警告音を再生しない。これにより、案内音声がマイクへ回り込んで音声キーワード認識を妨げることを避ける。ただし、一度でも本人確認に成功した後に有効期限切れなどで自動ロックされた状態でPTTボタンが押された（録音開始した）場合は、ボタンを押した瞬間に非同期で短い警告チャイム（警告音）を再生し、ユーザーにロック中であることを即座に通知する。起動直後の最初の本人確認時は、このボタン押下時の警告音再生は行わない。

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

GPIO入力は gpiozero のコールバックに処理を直接ぶら下げず、ポーリングした押下/解放エッジをキューに積み、別スレッドで順番にアプリへ渡す。これにより、録音開始やキャンセル処理中でも物理解放イベントを取り逃がしにくくする。

GPIO入力は起動直後の本人確認案内を読み上げる前に初期化する。これにより「本人確認してください」の読み上げ中にPTTを押した場合も、読み上げを止めて録音を開始できる。

## systemd ユニット

`systemd/argos.service` は Raspberry Pi 上で ARGOS を常駐させるための配布用ユニットファイルである。

- `User=pi`、`Group=pi` で実行する
- `WorkingDirectory=/home/pi/argos` を作業ディレクトリにする
- `EnvironmentFile=/home/pi/argos/.env` から設定を読み込む
- `ExecStart=/home/pi/argos/.venv/bin/argos` でプロジェクトの仮想環境内コマンドを起動する
- `PATH` に `/home/pi/.local/bin` を含め、Codex CLI を解決できるようにする
- `network-online.target`、`tailscale-online.target`、`autossh-clove.service`、`sound.target` の後に起動する
- 起動直後にTailscale越しのVOICEVOXなどへ早すぎる接続を行わないよう、`tailscale-online.target` と `autossh-clove.service` を `Wants` と `After` に含める
- 異常終了時は `Restart=on-failure` で再起動する

`systemd/argos-agent-runner.service` は Agent Runner をARGOS本体とは別に常駐させるための配布用ユニットファイルである。

- `User=pi`、`Group=pi` で実行する
- `WorkingDirectory=/home/pi/argos` を作業ディレクトリにする
- `EnvironmentFile=/home/pi/argos/.env` から設定を読み込む
- `ExecStart=/home/pi/argos/.venv/bin/argos-agent-runner` でRunnerを起動する
- 異常終了時は `Restart=on-failure` で再起動する

Runnerを使う場合は、`argos-agent-runner.service` を有効化したうえで、ARGOS本体側に `ARGOS_AGENT_RUNNER_URL=http://127.0.0.1:28765` と `ARGOS_AGENT_RUNNER_TOKEN` を設定する。Runnerを使わない場合、ARGOS本体は従来どおり直接エージェントCLIを起動する。

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
名前,provider,cwd[,voicevox_speaker]
```

- `名前`: 読み上げるスロット名
- `provider`: `codex` などのエージェント種別
- `cwd`: エージェントの作業ディレクトリ
- `voicevox_speaker`: 任意。指定した場合、このスロットの読み上げだけ指定VOICEVOX話者IDを使う

スロットを指定しない場合は、`ARGOS_AGENT_SLOT_NAME`、`ARGOS_AGENT_PROVIDER`、`ARGOS_AGENT_CWD` から既定スロットを作る。既定スロットのVOICEVOX話者IDは `ARGOS_AGENT_SLOT_VOICEVOX_SPEAKER` で指定できる。旧 `ARGOS_CODEX_SLOT_N` は互換のため読み込むが、新規設定では `ARGOS_AGENT_SLOT_N` を使う。

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
