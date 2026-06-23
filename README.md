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

ARGOSは `tailscale-online.target` と `autossh-clove.service` の後に起動します。Tailscale越しの VOICEVOX や周辺サービスを使う場合、起動直後に依存サービスへ早すぎる接続を行わないようにします。

状態確認とログ確認:

```bash
systemctl status argos.service
journalctl -u argos.service -f
```

設定を変更した場合は `.env` を更新してから再起動します。

```bash
sudo systemctl restart argos.service
```

複数のマイク候補を使う場合は、`.env` の `AUDIO_INPUT_DEVICES` にセミコロン区切りで指定します。`ARGOS_INPUT_DEVICES` と `ARGOS_AUDIO_INPUT_DEVICES` でも指定できます。録音開始時に接続済みの `CARD=...` を選びます。ALSAカード名に右側空白が含まれる場合も、空白を除いて照合します。

```text
AUDIO_INPUT_DEVICES=plughw:CARD=H2,DEV=0;plughw:CARD=Microphone,DEV=0
```

## PTT 操作

- PTT ON: 録音開始
- PTT OFF: 録音停止、文字起こし、Codex 実行、読み上げ
- 短押し1回: 録音を破棄
- 短押し2回: Codex スロット切替
- 処理中に短押し: 再生中の音声を止め、録音は破棄
- 処理中に押し続ける: 再生中の音声を止め、そのまま録音開始

処理中の読み上げを止めて次の録音を始めた場合、前の処理の終了タイミングでは録音中状態を維持し、ボタン解放で録音停止と送信へ進みます。
本人確認でロック中の場合は、短いキーワード発話を破棄しないよう、短押しでも録音として処理します。ただし短押し2回はロック中でも録音を破棄し、Codex スロット切替を優先します。
録音WAVは `/tmp/argos/utterance-*.wav` のユニークな一時ファイルとして作成し、STT処理後に削除します。STTゲートウェイへ送るmultipartファイル名も実際の録音ファイル名に合わせます。これにより、次の録音開始で前の録音ファイルを消してしまう競合を避けます。起動時には古い録音一時ファイルも掃除します。
短い本人確認キーワードがSTTで空文字になりにくいよう、録音停止後にWAVの前後へ短い無音を追加してから文字起こしへ渡します。

## 読み上げ

Codex の応答は `--json` の JSONL イベントから取得し、句読点や改行で分割して VOICEVOX に順次投入します。キャンセル時は再生中の音声と未再生チャンクを破棄します。

`ARGOS_LCD_ENABLED=true` の場合、読み上げる文を ST7789 LCD にも表示します。日本語表示には IPA 系フォントを使います。夜間でも明るくなりすぎないよう、LCDは黒背景に白文字で表示します。

既定の区切り文字:

- `。`
- `！`
- `？`
- `!`
- `?`
- 改行

`.` は `README.md` や `systemd.service` などを途中で分割しないよう、既定では区切り文字に含めません。変更する場合は `.env` の `ARGOS_TTS_DELIMITERS` に区切り文字を並べます。改行は常に区切りとして扱います。

Codex CLI が途中イベントを出した場合は、その差分から順に処理します。CLI 側が最終回答までイベントを出さない場合でも、最終回答は上記の区切りで分割して読み上げます。

短い読み上げ文はTTSキャッシュに保存し、同じテキストとVOICEVOX話者IDの組み合わせでは再合成を省略します。`ARGOS_TTS_CACHE_ENABLED=false` で無効化できます。

Codex を呼び出した直後は、作業を始めたことを短い音声で通知します。応答が遅い場合は、待機中であることを一定間隔で追加通知します。通知文はAI名を出さず、「確認するね」や「もう少し待ってね」のように音声で聞きやすい短い言い方を複数候補からランダムに選びます。待機通知の再生中にCodex本文が届いた場合は、待機通知の再生完了後に本文を読み上げます。

最初の発話処理時は、前回の利用時刻に応じて短い挨拶を読み上げます。10分以内の再利用では挨拶を省略し、日付が変わった場合は時間帯に合わせて挨拶します。状態は `ARGOS_GREETING_STATE_PATH` のJSONへ保存します。

ARGOS 起動時は、HDMIダッシュボードに短いスプラッシュアニメーションを表示し、VOICEVOXを使わない合成起動音を1回再生します。`ARGOS_STARTUP_SPLASH_ENABLED` と `ARGOS_STARTUP_SOUND_ENABLED` で無効化できます。

## 本人確認

`ARGOS_AUTH_ENABLED=true` にすると、本人確認が済むまで発話をCodexへ送りません。現段階では音声キーワード解除に対応しています。キーワードは平文では保存せず、次のコマンドでハッシュ化して `.env` の `ARGOS_AUTH_KEYWORD_HASH` に設定します。STTの表記ゆれを許可したい場合は、複数のハッシュをセミコロン区切りで設定できます。
本人確認の繰り返し案内や警告音は、PTT録音中には再生しません。案内音声がマイクへ回り込んでキーワード認識を妨げることを避けるためです。

```bash
uv run scripts/hash-auth-keyword.py
```

ロック中の発話は文字起こしだけに使い、キーワードが一致した場合はロックを解除します。解除キーワードそのものはCodexへ送りません。認証済みの有効期限が待機中に切れた場合は、ダッシュボード表示も自動で「ロック中」へ戻ります。
ロック中にPTTを押している間は、ダッシュボードに「本人確認録音中」と表示します。PTTを離した後は「本人確認中」と表示し、押下が認識されたかを画面で確認できるようにします。

カメラ照合を使う場合は、まず顔サンプルを登録します。

```bash
uv run scripts/check-face-detection.py
uv run scripts/enroll-face-auth.py --count 5
```

最初に `check-face-detection.py` で顔が1件検出されるか確認します。登録時は顔が1つだけ検出された画像から、顔領域だけの指紋を保存します。その後、`.env` で `ARGOS_AUTH_FACE_ENABLED=true` にします。起動時とロック中の発話時にカメラ照合を試し、成功した場合はその発話をそのままCodexへ送ります。失敗した場合は音声キーワードで解除できます。
顔検出にはOpenCVが必要です。未導入の場合、顔認証は失敗扱いになり、音声キーワード解除へ戻ります。
カメラが横向きに写る場合は `ARGOS_AUTH_FACE_IMAGE_ROTATION=90` のように設定します。

起動後に未認証の場合は「本人確認をしてください。」と案内します。`ARGOS_AUTH_WARNING_DELAY_SECONDS` の秒数が過ぎても未認証なら、警告音と本人確認案内を繰り返します。`ARGOS_AUTH_ALERT_DELAY_SECONDS` を超えると「警戒モードに入りました」と案内し、画面を「警戒中」にします。本人確認の失敗が続いた場合は、`ARGOS_AUTH_ALERT_COMMAND` に設定したコマンドも実行できます。

音声キーワード照合時は、STTで認識された文字列と照合結果を `本人確認キーワード照合` としてログに出します。期待キーワードそのものはログに出しません。

設定:

```text
ARGOS_CODEX_BYPASS_SANDBOX=false
ARGOS_CODEX_PROGRESS_VOICE=true
ARGOS_CODEX_PROGRESS_FIRST_DELAY_SECONDS=8
ARGOS_CODEX_PROGRESS_INTERVAL_SECONDS=20
ARGOS_LCD_ENABLED=false
ARGOS_LCD_FONT_PATH=/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf
```

GPIO や SPI など、Codex がホストのデバイスを直接触る必要がある場合は `ARGOS_CODEX_BYPASS_SANDBOX=true` にします。この場合、Codex CLI に `--dangerously-bypass-approvals-and-sandbox` を渡します。

## HDMI ダッシュボード

`ARGOS_DASHBOARD_ENABLED=true` にすると、横長HDMI画面向けのダッシュボードを起動します。

```text
ARGOS_DASHBOARD_ENABLED=true
ARGOS_DASHBOARD_HOST=0.0.0.0
ARGOS_DASHBOARD_PORT=8765
ARGOS_DASHBOARD_TOKEN=<ランダムなトークン>
ARGOS_DASHBOARD_SCREENSAVER_SECONDS=300
```

ブラウザで `http://localhost:8765/` を開くと、ARGOSの状態、現在のエージェントスロット、会話履歴、外部通知を表示します。1920x440では3列、狭い画面では通知欄が下へ回り込みます。
会話更新時は通知欄を再描画しないため、表示中の画像を安定して保持します。
中央の会話欄と右側の通知欄はタッチ操作で縦にスクロールできます。会話欄は現在のエージェントスロットごとに切り替わり、末尾を表示しているときだけ新しい会話へ自動追従するため、過去ログを読んでいる途中で表示位置は変わりません。左側パネルにはスロット一覧を横並びチップで表示し、スロット数が増えた場合は一覧だけを横にスクロールできます。裏で完了したスロットは未読表示にします。表示中ではないスロットの応答は読み上げず、そのスロットへ切り替えたときに句読点単位で分割して読み上げます。
`ARGOS_DASHBOARD_SCREENSAVER_SECONDS` 秒間操作がない場合は、ロック中も黒い全画面表示へ切り替わります。0以下にすると無効化できます。現段階ではバックライトやHDMI出力は消しません。タッチ操作に加えて、PTT録音開始でも黒表示を解除します。
地図オーバーレイの現在地は、既定ではローカルのgpsdまたはGPSデバイスから取得します。外部端末のGPS APIを使う場合は、`.env` で `ARGOS_LOCATION_PROVIDER=remote` と `ARGOS_REMOTE_LOCATION_URL` を設定します。
左側のミュートボタンで読み上げを一時停止できます。ミュート中は再生中の音声を止め、解除後はキューに残っている読み上げから再開します。ミュート状態はボタン表示で示し、録音中などの動作表示はそのまま維持します。
左側のフォントサイズボタンで、ダッシュボードの主要テキストを `小`、`中`、`大` から切り替えられます。選択値はブラウザのローカルストレージに保存され、キオスク画面の再読み込み後も維持されます。
`ARGOS_AGENT_USAGE_COMMAND_<PROVIDER>` にJSONを返すコマンドを設定すると、現在のエージェントがそのproviderの時だけ左側パネルへ週間・月間の利用枠を表示します。例: `ARGOS_AGENT_USAGE_COMMAND_CODEX=/home/yuki/bin/codex-usage-status`
文字起こし、Codex、TTSフィルター、VOICEVOX、音声再生で内部エラーが起きた場合は、右側の通知欄へ赤色で表示します。同じエラーが連続した場合は1件にまとめます。

`/tmp/argos/camera-latest.jpg` に静止画を置くと、`/camera/latest.jpg` で配信できます。通知の `image_url` にこのURLを指定すると、カメラ画像も表示できます。

ChromiumでHDMI画面へ全画面表示する場合:

```bash
./scripts/open-dashboard-kiosk.sh
```

キオスク画面は専用のChromiumプロフィールを日本語モードで使います。OSキーリングと翻訳UIは使用せず、ダッシュボード上のマウスカーソルも非表示にします。インストーラーは翻訳バーを無効化するChromium管理ポリシーを `/etc/chromium/policies/managed/` に配置します。

ユーザーsystemdで自動表示する場合:

```bash
./scripts/install-dashboard-autostart.sh
```

インストール後は `systemctl --user status argos-dashboard-kiosk.service` で状態を確認できます。

外部サービスから通知を追加する場合:

```bash
curl -X POST http://<raspberry-pi>:8765/api/events \
  -H "Authorization: Bearer <ARGOS_DASHBOARD_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"type":"notification","source":"mail","title":"新着メール","text":"確認が必要です"}'
```

通知では `image_url` と `link_url` も指定できます。会話追加は `user_message` または `agent_message`、状態更新は `status`、通知削除は `clear_notifications` を `type` に指定します。
通知イベントに `sound: true` や `speak: true` を付けると、ARGOS本体が画面を起こし、通知音または通知本文の読み上げを行います。
ダッシュボードのミュート操作は `POST /api/control` を使い、`action` に `mute`、`unmute`、`toggle_mute` を指定します。読み上げ音量は左側の縦スライダーで変更でき、同じAPIへ `{"action":"set_volume","volume":55}` のように送信します。このAPIも `ARGOS_DASHBOARD_TOKEN` によるBearer認証が必要です。変更した音量とミュート状態は `ARGOS_AUDIO_STATE_PATH` に保存し、ARGOS再起動後も前回の状態を復元します。

`ARGOS_DASHBOARD_HOST=0.0.0.0` ではLAN内の他端末から画面も閲覧できます。会話履歴を含むため、インターネットへ直接公開しないでください。

## 必要な外部サービス

- stt-gateway: `POST /transcribe`
- tts-filter: `POST /normalize`
- VOICEVOX Engine: `POST /audio_query` と `POST /synthesis`
- Codex CLI: `codex exec` と `codex exec resume`
- Hermes Agent CLI: `hermes chat -q`

Codex のセッションIDは `CODEX_HOME/argos-sessions.json` にスロットごとに保存します。`--json` の標準出力にセッションIDが出ない場合は、`CODEX_HOME/sessions` の直近セッションファイルからIDを補完します。サービス再起動後も保存済みIDを使って同じセッションを再開します。`/reset` を入力すると、現在スロットの保存済みIDも削除します。

Hermes を使う場合は `ARGOS_AGENT_PROVIDER=hermes`、または `ARGOS_AGENT_SLOT_N=名前,hermes,/path/to/workdir` を指定します。スロットごとにVOICEVOX話者を変える場合は4項目目に話者IDを指定し、例えば `ARGOS_AGENT_SLOT_1=調査,hermes,/path/to/workdir,8` のように設定します。ARGOS は `hermes chat -q <prompt> -Q --source argos` を実行し、出力に含まれる session ID を `ARGOS_AGENT_STATE_PATH` に保存して次回以降 `--resume` で再開します。

ARGOS本体の再起動で実行中エージェントを巻き込まない構成にする場合は、別サービスとして Agent Runner を起動します。

```bash
uv run argos-agent-runner
```

ARGOS本体側には `ARGOS_AGENT_RUNNER_URL=http://127.0.0.1:28765` と `ARGOS_AGENT_RUNNER_TOKEN` を設定します。Runnerはジョブごとに状態、標準出力、最終回答、配信済み状態を `ARGOS_AGENT_RUNNER_STATE_DIR` に保存します。

外部仕様と設定の詳細は [docs/basic_design.md](docs/basic_design.md) を参照してください。
