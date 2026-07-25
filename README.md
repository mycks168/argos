# ARGOS

ARGOS（Autonomous Road Guardian & Observation System）は、Raspberry Pi の PTT スイッチで録音し、stt-gateway、Codex CLI、tts-filter、VOICEVOX をつないで音声で Codex を操作するエージェントです。

## 事前準備

ARGOSのインストーラ自体を実行する端末には、先に `uv` を入れておきます。macOS/Linux では公式のスタンドアロンインストーラを使えます。

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
```

`--bootstrap` はARGOS実行ユーザーにも `uv` を導入しますが、`uv run argos-install ...` を起動する現在のユーザーには事前に `uv` が必要です。

Codex、Antigravity、Claude を使う場合、各CLIの導入とログインはインストーラでは自動化しません。`argos-install --bootstrap` 後に、ARGOS実行ユーザーで必要なCLIを入れて、初回ログインまで済ませてください。

```bash
sudo -iu argos

# Codex CLI
curl -fsSL https://chatgpt.com/codex/install.sh | sh
codex

# Antigravity CLI
curl -fsSL https://antigravity.google/cli/install.sh | bash
agy

# Claude Code
curl -fsSL https://claude.ai/install.sh | bash
claude
```

インストール後は `command -v codex`、`command -v agy`、`command -v claude` で、ARGOS実行ユーザーのPATHから見えることを確認します。

公式手順:

- uv: <https://docs.astral.sh/uv/getting-started/installation/>
- Codex CLI: <https://developers.openai.com/codex/cli>
- Antigravity CLI: <https://github.com/google-antigravity/antigravity-cli>
- Claude Code: <https://code.claude.com/docs/en/quickstart>

## ARGOS専用機として初期化する場合

別PCやRaspberry PiをARGOS専用機として初期化する場合は、リポジトリを `/opt/argos` に配置して、一式インストーラを `--bootstrap --configure --apply` で実行します。`argos` ユーザー作成、OSパッケージ導入、ARGOS実行ユーザー向け `uv` 導入、デバイス権限付与、systemd unit生成、enable/startまでまとめて行います。

```bash
sudo git clone https://github.com/mycks168/argos.git /opt/argos
cd /opt/argos
sudo env "PATH=$PATH" uv run argos-install --bootstrap --configure --apply
```

Ubuntu 26など、システムのPythonが3.13以降の場合も同じコマンドを使用できます。ARGOSは
`lgpio`の公式wheelが提供されるPython 3.11/3.12を対象としており、互換Pythonがない場合は
`uv`が自動的にダウンロードして使用します。通常、システムPythonの入れ替えは不要です。

`develop` など未リリースブランチを検証する場合だけ、clone後に対象ブランチへ切り替えてからインストーラを実行してください。通常の導入手順はブランチ名に依存しません。

インストール後はARGOS実行ユーザーで、利用するエージェントCLIの初回ログインを済ませます。

```bash
sudo -iu argos
codex
agy
claude
hermes
```

状態確認とログ確認:

```bash
systemctl status argos.service
journalctl -u argos.service -f
```

## 更新する場合

更新する場合は、`/opt/argos` で次を実行します。Git pull、依存更新、systemd unit再生成、既定サービス再起動まで行います。既存の `config.yaml` は上書きしません。

```bash
cd /opt/argos
sudo env "PATH=$PATH" uv run argos-install --update
```

設定を変更した場合は `config.yaml` を更新してから再起動します。ARGOS本体の設定は機能別の階層へ記載し、スキルへ渡す`SLACK_WEBHOOK_URL`などの汎用値だけ`environment`へ記載します。環境変数は一時的な上書きに使え、旧 `.env` も移行互換として読み込まれます。詳しい形式と優先順位は [docs/basic_design.md](docs/basic_design.md#設定ファイル) を参照してください。

既存の `.env` だけを `config.yaml` へ変換する場合は、次を実行します。インストール、依存更新、サービス再起動は行わず、既存の `config.yaml` がある場合は上書きせず終了します。

```bash
cd /opt/argos
uv run argos-install --migrate-config
```

```bash
sudo systemctl restart argos.service
```

## ARGOS一式インストーラ

ARGOS本体、Agent Runner、TTSフィルター、相槌API、リマインダー、スキルなどをまとめて導入するためのインストーラです。まず計画だけ確認する場合は、引数なしまたは `--json` で実行します。

```bash
uv run argos-install
uv run argos-install --json
```

既存環境で `config.yaml` 作成、`uv sync`、systemd unit生成、enable/startまで行う場合:

```bash
uv run argos-install --configure --apply
```

`--bootstrap` は `argos` ユーザーがなければ作成し、`ffmpeg`、`build-essential`、`python3-dev`、`swig`、`liblgpio-dev`、`cron`、IPAフォント、Chromiumなどを導入して、kioskを含む構成ではXorg、LightDM、Openboxも導入して`argos`ユーザーで画面セッションへ自動ログインし、`/opt/argos` の所有者も `argos:argos` に揃えます。`ffmpeg` はSTT Gateway向けのOpus変換にも使用します。`uv` はARGOS実行ユーザーの `~/.local/bin` へ導入し、ARGOS本体は `uv sync --extra face` で顔認証用OpenCVも含めて同期します。user serviceが使う `~/.config`、`~/.local`、`~/.cache` の所有者も補正します。

`--configure` はSTTゲートウェイ、VOICEVOX、OSRM、GPS API、マイク、スピーカーなどを対話式に `config.yaml` へ設定します。旧 `.env` がある場合は内容を失わず自動移行します。利用するエージェントproviderを選んだうえで、ダッシュボードに出す会話スロット名、作業ディレクトリ、VOICEVOX話者IDも設定できます。Codex、Antigravity、Claude、HermesなどのOAuth認証は自動化しません。

別のARGOSを会話スロットとして使う場合は、`config.yaml`の`agent.slots`へ`type: remote`のスロットを追加します。ローカルスロットと同じ配列へ任意の順番で配置できます。設定例は [docs/basic_design.md](docs/basic_design.md#リモートargosスロット) を参照してください。

対象サービスと取り込み方針は [docs/bundled_installer.md](docs/bundled_installer.md) を参照してください。
ウェイクワード用のONNXモデルは `models/wakeword/` に同梱しているため、`ARGOS_WAKEWORD_MODEL_DIR=models/wakeword` の既定値で利用できます。

複数のマイク候補を使う場合は、`config.yaml` の `audio.input_devices` に配列で指定します。録音開始時に接続済みの `CARD=...` を選びます。ALSAカード名に右側空白が含まれる場合も、空白を除いて照合します。

```yaml
audio:
  input_devices:
    - default
    - plughw:CARD=USBMic,DEV=0
```

## PTT 操作

`ARGOS_PTT_GPIO` にBCM番号を指定するとGPIOのPTTスイッチを使います。UbuntuなどGPIOがない環境では空欄にするとGPIO入力を初期化しません。

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

## ウェイクワード

`ARGOS_WAKEWORD_ENABLED=true` にすると、LiveKit形式のONNXモデルで「アルゴス」を常時監視します。検知後は同じマイクストリームから発話をWAV化し、既存の文字起こし、エージェント、読み上げ処理へ渡します。検知は2秒窓を無音で前詰めして早い段階から開始し、短い発話の先頭を取りこぼさないよう、既定で検知直前3秒の音声もWAV先頭へ含めます。発話終了は既定でSilero VADを使います。PTT操作は引き続き使えます。

物理ミュート付きマイクで呼びかけを省略する場合は、`ARGOS_LISTEN_MODE=vad` を設定します。ミュート解除後にSilero VADが発話を検知すると録音を開始し、無音で終了して通常の文字起こしと応答へ渡します。既定の `wakeword` モードには影響しません。
自宅などSTTゲートウェイが近く誤検知を抑えたい環境では、`ARGOS_WAKEWORD_REQUIRE_STT_WAKEWORD=true` にすると、文字起こし結果が「アルゴス」などの呼びかけから始まる場合だけ処理します。ダッシュボードのマイクOFFボタンを押すと、PTTとウェイクワードの受付を一時停止できます。

誤検知で破棄された録音は、既定で `/tmp/argos/wakeword-candidates/hard_negative/` に音声と判定理由を保存します。周囲の会話を誤検知候補として判別するには `ARGOS_WAKEWORD_REQUIRE_STT_WAKEWORD=true` も有効にします。内容を聞いて誤検知だと確認したものだけ、wakeword trainerの `raw-dataset/hard_negative/` へ移してください。保存を止める場合は `ARGOS_WAKEWORD_FALSE_POSITIVE_CAPTURE=false`、保存先を変える場合は `ARGOS_WAKEWORD_FALSE_POSITIVE_DIR` を設定します。`/tmp` 配下は再起動で消えるため、確認前に必要な候補を退避してください。

応答の読み上げが終わったあと、`ARGOS_WAKEWORD_FOLLOWUP_SECONDS`（既定3秒、0で無効）だけウェイクワードを言い直さずに続けて話せます。この間に話しかけると、そのまま次の発話として受け付け、応答のたびに窓が開き直すので会話が続きます。この追いかけ受付は本人確認済みのときだけ有効で、無音のまま数秒たつと通常の待機に戻ります。

`ARGOS_WAKEWORD_BARGEIN_ENABLED=true` にすると、読み上げ中でもウェイクワードで割り込んで（バージイン）、進行中の読み上げを止めて次の発話を受け付けられます。ウェイクワードonly運用でTTSを止める手段が無い問題への対策です。ただし自分の声に反応しないよう、マイク入力を音響エコーキャンセル(AEC)済みの経路にすることが前提です。`scripts/setup-echo-cancel.sh`（Raspberry Pi OS / Ubuntu 共通、`--install-deps`で依存導入、`--revert`で撤去）でPipeWireのエコーキャンセルとALSAブリッジを設定します（詳細は `docs/basic_design.md`）。既定は無効です。

モデルは `ARGOS_WAKEWORD_MODEL_DIR` に配置します。

Raspberry Pi AI HAT+のHailo-8を使う場合は、Hailo用にコンパイルした`embedding.hef`を配置し、`ARGOS_WAKEWORD_EMBEDDING_HEF=models/wakeword/embedding.hef`を設定します。メル特徴量と最終分類はONNX Runtime、負荷の大きい音声埋め込みはHailoで実行します。HEF設定が空の場合は従来どおり全段をCPUで実行します。

```text
models/wakeword/
  argos.onnx
  melspectrogram.onnx
  embedding_model.onnx
  argos_eval.json
  silero_vad_v6.onnx
```

既定は無効です。車内ノイズで誤検知する場合は `ARGOS_WAKEWORD_THRESHOLD` を上げます。VADモデルを別の場所に置く場合は `ARGOS_WAKEWORD_VAD_MODEL` で指定します。ONNX Runtime は標準依存として入ります。

## 読み上げ

Codex の応答は `--json` の JSONL イベントから取得し、句読点や改行で分割して VOICEVOX に順次投入します。キャンセル時は再生中の音声と未再生チャンクを破棄します。

エージェント利用枠表示は `services/agent-limit/update_limits.py` が生成するJSONを読みます。インストーラーはこの更新スクリプトを5分おきに動かすcronをARGOS実行ユーザーへ登録します。

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
顔検出にはOpenCVが必要です。`argos-install --apply` または `--update` で導入したARGOS本体の `.venv` には、顔認証用OpenCVも入ります。未導入の場合、顔認証は失敗扱いになり、音声キーワード解除へ戻ります。
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

`ARGOS_DASHBOARD_TOKEN` が空の場合、`argos-install --apply` または `--update` 実行時にランダムなトークンを自動生成します。インストーラーは同じ値を `services/argos-reminder/.env` にも反映し、リマインダー通知がダッシュボードAPIで401にならないようにします。ミュート、マイクOFF、音量変更などの画面操作は、このトークンで保護された `/api/control` を使います。

ブラウザで `http://localhost:8765/` を開くと、ARGOSの状態、現在のエージェントスロット、Wi-Fi接続状態、会話履歴、外部通知を表示します。1920x440では3列、800x600程度では通知欄を右側に残すコンパクト3列、さらに狭い画面では通知欄を下へ回り込ませます。
会話更新時は通知欄を再描画しないため、表示中の画像を安定して保持します。
中央の会話欄と右側の通知欄はタッチ操作で縦にスクロールできます。会話欄は現在のエージェントスロットごとに切り替わり、末尾を表示しているときだけ新しい会話へ自動追従するため、過去ログを読んでいる途中で表示位置は変わりません。左側パネルの `CURRENT SLOT` 表示をタップすると全スロットの一覧が開きます。裏で完了したスロットは未読表示にします。表示中ではないスロットの応答は読み上げず、そのスロットへ切り替えたときに句読点単位で分割して読み上げます。
`ARGOS_DASHBOARD_SCREENSAVER_SECONDS` 秒間操作がない場合は、ロック中も黒い全画面表示へ切り替わります。0以下にすると無効化できます。現段階ではバックライトやHDMI出力は消しません。タッチ操作に加えて、PTT録音開始でも黒表示を解除します。
地図オーバーレイの現在地は、既定ではローカルのgpsdまたはGPSデバイスから取得します。外部端末のGPS APIを使う場合は、`.env` で `ARGOS_LOCATION_PROVIDER=remote` と `ARGOS_REMOTE_LOCATION_URL` を設定します。
ARGOSロゴ直下のミュートボタンで読み上げを一時停止できます。ミュート中は再生中の音声を止め、解除後はキューに残っている読み上げから再開します。ミュート状態はボタン表示で示し、録音中などの動作表示はそのまま維持します。
会話欄下部の入力欄からテキストを送ると、現在のエージェントスロットへ同じ会話の続きとして入力できます。この操作ではSTTとTTSを使わず、回答も会話欄へ文字で表示します。iPhoneやiPadなど、同じダッシュボードへ接続できるブラウザからも利用できます。
左側の `CURRENT SLOT` 表示をクリックまたはタップして一覧から選ぶと、そのスロットへ切り替えて会話履歴を表示できます。`agent.slots`の`ptt_cycle: false`を指定したスロットは、画面から選択できますがPTTダブルクリックの巡回対象にはなりません。

`conversation_history.enabled: true`にすると、回答完了時にスロット別の画面会話履歴を保存し、ARGOS再起動後に復元します。設定画面の「履歴を引き継いで新規セッション」は、現在の履歴をエージェントに要約させてセッションをリセットし、次回の入力へ要約を一度だけ付与します。この機能は`conversation_memory.enabled: true`の場合に利用できます。
回答処理中でも別の空いているスロットへ切り替えて、テキスト会話を続けられます。裏のスロットで回答が完了すると、そのスロットが未読表示になります。
スマートフォンやタブレットでは `/sp` を開くと、本文をスクロールさせない専用表示を利用できます。左上のボタンで状態・スロット、右上のベルで通知を開閉します。通知バッジは未確認件数を示し、通知欄を開くと履歴を残したまま端末ごとに確認済みになります。
HTTPSで開いた `/sp` では、入力欄横のマイクボタンをタップして録音を開始し、もう一度タップして音声を送信できます。回答音声は同じブラウザで順次再生されます。初回はSafariなどのマイク利用許可が必要です。
同じ操作行のマイクOFFボタンで、PTTとウェイクワードの受付を一時停止できます。もう一度押すとマイク受付を再開します。Wi-Fi状態は接続中だけARGOSロゴ横に表示します。
左側のフォントサイズボタンで、ダッシュボードの主要テキストを `小`、`中`、`大` から切り替えられます。選択値はブラウザのローカルストレージに保存され、キオスク画面の再読み込み後も維持されます。未保存時の初期値は `ARGOS_DASHBOARD_DEFAULT_FONT_SIZE` で指定できます。
`ARGOS_AGENT_USAGE_COMMAND_<PROVIDER>` にJSONを返すコマンドを設定すると、現在のエージェントがそのproviderの時だけ左側パネルへ週間・月間の利用枠を表示します。リモートスロットでは `remote_provider` のローカル利用枠を表示します。例: `ARGOS_AGENT_USAGE_COMMAND_CODEX=/opt/argos/bin/codex-usage-status`
文字起こし、Codex、TTSフィルター、VOICEVOX、音声再生で内部エラーが起きた場合は、右側の通知欄へ赤色で表示します。同じエラーが連続した場合は1件にまとめます。

`/tmp/argos/camera-latest.jpg` に静止画を置くと、`/camera/latest.jpg` で配信できます。通知の `image_url` にこのURLを指定すると、カメラ画像も表示できます。

ChromiumでHDMI画面へ全画面表示する場合:

```bash
./scripts/open-dashboard-kiosk.sh
```

キオスク画面は専用のChromiumプロフィールを日本語モードで使います。OSキーリング、翻訳UI、Googleサインイン、同期UIは使用せず、ダッシュボード上のマウスカーソルも非表示にします。インストーラーはChromium管理ポリシーを `/etc/chromium/policies/managed/` と `/etc/chromium-browser/policies/managed/` に配置します。起動スクリプトはUbuntuとRaspberry Pi OSの両方を考慮し、`xset` と `gsettings` でスクリーンセーバーとロック画面を可能な範囲で無効化します。

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

`display:"center"` を付けると、右の通知欄だけでなく画面中央に大きなアラートを重ねて表示します。「ご飯だよ〜」のような全員へ強く見せたい連絡向けです。`duration_seconds` に秒数を指定すると自動で閉じ、未指定なら画面タップで閉じます。中央アラートは `{"type":"clear_center_alert"}` でも消せます。

```bash
curl -X POST http://<raspberry-pi>:8765/api/events \
  -H "Authorization: Bearer <ARGOS_DASHBOARD_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"type":"notification","source":"kitchen","title":"ご飯だよ〜","display":"center","duration_seconds":30,"speak":true}'
```

通知に画像を添付したい場合は、先に画像をアップロードして得たURLを `image_url` に指定します。

```bash
url=$(curl -s -X POST http://<raspberry-pi>:8765/api/uploads \
  -H "Authorization: Bearer <ARGOS_DASHBOARD_TOKEN>" \
  -H "Content-Type: image/jpeg" --data-binary @photo.jpg | python3 -c 'import sys,json;print(json.load(sys.stdin)["url"])')
curl -X POST http://<raspberry-pi>:8765/api/events \
  -H "Authorization: Bearer <ARGOS_DASHBOARD_TOKEN>" \
  -H "Content-Type: application/json" \
  -d "{\"type\":\"notification\",\"title\":\"写真が届いたよ\",\"display\":\"center\",\"image_url\":\"$url\"}"
```

アップロード画像は `ARGOS_DASHBOARD_UPLOAD_DIR`（既定 `/tmp/argos/uploads`）へ保存し、`ARGOS_DASHBOARD_UPLOAD_MAX_BYTES`（既定5MB）を超える画像は拒否、`ARGOS_DASHBOARD_UPLOAD_KEEP`（既定50件）を超えた古い画像は自動削除します。複数端末へ一斉通知したい場合は、当面は送信側で各端末の `/api/events` を順に叩いてください。
ダッシュボードのミュート操作は `POST /api/control` を使い、`action` に `mute`、`unmute`、`toggle_mute` を指定します。読み上げ音量は左側の縦スライダーで変更でき、同じAPIへ `{"action":"set_volume","volume":55}` のように送信します。このAPIも `ARGOS_DASHBOARD_TOKEN` によるBearer認証が必要です。変更した音量とミュート状態は `ARGOS_AUDIO_STATE_PATH` に保存し、ARGOS再起動後も前回の状態を復元します。

ttyd がインストール済みなら、tmux セッションを中央または右ペインへ表示できます。既定では `127.0.0.1:7681` に ttyd を起動し、`argos-terminal` セッションを iframe 表示します。Webターミナルはシェル操作権限を持つため、外部公開せずローカル表示に閉じてください。

```bash
uv run scripts/show-ttyd-tmux-overlay.py --target-slot center --replace-top
```

`ARGOS_DASHBOARD_HOST=0.0.0.0` ではLAN内の他端末から画面も閲覧できます。会話履歴を含むため、インターネットへ直接公開しないでください。

## 必要な外部サービス

- stt-gateway: `POST /transcribe`
- tts-filter: `POST /normalize`
- VOICEVOX Engine: `POST /audio_query` と `POST /synthesis`
- Codex CLI: `codex exec` と `codex exec resume`
- Hermes Agent CLI: `hermes chat -q`

Codex のセッションIDは `ARGOS_AGENT_STATE_PATH` にスロットごとに保存します。`--json` の標準出力にセッションIDが出ない場合は、`CODEX_HOME/sessions` の直近セッションファイルからIDを補完します。サービス再起動後も保存済みIDを使って同じセッションを再開します。`/reset` を入力すると、現在スロットの保存済みIDも削除します。

ARGOS共通のシステム指示は各スロットの会話開始時だけエージェントへ渡します。追加指示は `ARGOS_AGENT_SYSTEM_PROMPT` または `ARGOS_AGENT_SYSTEM_PROMPT_FILE`、スキル配置場所は `ARGOS_AGENT_SKILLS_DIR` で指定できます。注入済み状態は `ARGOS_AGENT_SYSTEM_PROMPT_STATE_PATH` に保存し、`/reset` 後は再度注入します。

Hermes を使う場合は `ARGOS_AGENT_PROVIDER=hermes`、または `ARGOS_AGENT_SLOT_N=名前,hermes,/path/to/workdir` を指定します。スロットごとにVOICEVOX話者を変える場合は4項目目、モデルを変える場合は5項目目を使い、例えば `ARGOS_AGENT_SLOT_1=調査,hermes,/path/to/workdir,8,model-name` のように設定します。話者を省略してモデルだけ指定する場合は4項目目を空にします。従来の3・4項目形式もそのまま利用できます。ARGOS は `hermes chat -q <prompt> -Q --source argos` を実行し、出力に含まれる session ID を `ARGOS_AGENT_STATE_PATH` に保存して次回以降 `--resume` で再開します。

ARGOS本体の再起動で実行中エージェントを巻き込まない構成にする場合は、別サービスとして Agent Runner を起動します。

```bash
uv run argos-agent-runner
```

ARGOS本体側には `ARGOS_AGENT_RUNNER_URL=http://127.0.0.1:28765` と `ARGOS_AGENT_RUNNER_TOKEN` を設定します。Runnerはジョブごとに状態、標準出力、最終回答、配信済み状態を `ARGOS_AGENT_RUNNER_STATE_DIR` に保存します。

外部仕様と設定の詳細は [docs/basic_design.md](docs/basic_design.md) を参照してください。
