# ARGOS 利用者マニュアル

ARGOS の使い方と設定方法をまとめた文書です。導入手順は [README](../README.md)、設定項目の全一覧は [基本設計](basic_design.md) を参照してください。

ARGOS には**声**と**画面**という 2 つの入口があります。どちらからでも同じエージェントと同じ会話を続けられます。

---

## 1. 設定の変え方

設定の置き場所は 3 つあります。

| どこ | 効く範囲 | 反映 |
| --- | --- | --- |
| `/opt/argos/config.yaml` | ARGOS 全体 | 再起動が必要 |
| `/settings` 画面 | ARGOS 全体（`config.yaml` の全項目） | 保存後に再起動が必要 |
| ダッシュボードのメニュー | そのブラウザだけ | すぐ反映 |

`/settings` は `config.yaml` を画面から編集するための入口です。保存しても
すぐには効かず、「保存しました。反映にはARGOSの再起動が必要です。」と表示されます。

### config.yaml を編集する

雛形は `config.yaml.example` にあります。認証情報を含むため、権限は 600 のままにしてください。

```bash
sudo -iu argos
vi /opt/argos/config.yaml
exit
sudo systemctl restart argos.service
```

書き方は機能別の階層です。以下、各章に必要な設定を載せます。

```yaml
audio:
  output_volume: 70
wakeword:
  enabled: true
```

環境変数でも一時的に上書きできます。旧形式の `.env` も移行互換として読み込みます。

### 設定を間違えたとき

起動に失敗した場合はログに理由が出ます。

```bash
journalctl -u argos.service -n 50
```

---

## 2. 声で話しかける

### 呼びかけて話す

既定では「**アルゴス**」と呼びかけると待ち受けが始まります。呼びかけたあと、そのまま用件を話してください。話し終えて数秒黙ると録音が終わり、答えが返ってきます。

答えの読み上げが終わったあと数秒間は、**もう一度呼びかけなくてもそのまま話しかけられます**。この間に話せば会話が続きます。黙っていれば通常の待機に戻ります。この続け話しは本人確認が済んでいるときだけ有効です。

**設定**

```yaml
wakeword:
  enabled: true
  model_dir: models/wakeword     # 呼びかけ判定のONNXモデル置き場
  threshold: 0.5                 # 上げるほど鳴りにくい
  followup_seconds: 3.0          # 続け話しを受け付ける秒数。0で無効
  aliases: []                    # 「アルゴス」以外の呼び方を足す
```

モデルはリポジトリに同梱しているので、既定のままで動きます。

### 読み上げ中に割り込む

読み上げの途中で「アルゴス」と呼んで止められます。既定は無効です。

```yaml
wakeword:
  bargein_enabled: true
```

**マイク入力がエコーキャンセル（AEC）済みであることが前提です。** そうでないと自分の読み上げ音声に反応します。設定用スクリプトを用意しています。

```bash
./scripts/setup-echo-cancel.sh --install-deps    # 依存導入と設定
./scripts/setup-echo-cancel.sh --revert          # 元に戻す
```

### 音を検知したら録音する

物理ミュート付きマイクを使っていて呼びかけを省きたい場合は、待ち受け方法を変えます。ミュートを解除して話し始めると、そのまま録音が始まります。

```yaml
audio:
  listen_mode: vad      # wakeword（既定）または vad
```

`/settings` の「マイク・スピーカー」からも切り替えられます（保存後に再起動が必要です）。

### ボタンで話す（PTT）

押している間だけ録音する方式です。3 通りあります。

| 方法 | 使い方 | 必要な設定 |
| --- | --- | --- |
| GPIO スイッチ | 押している間が録音 | `ptt.gpio` に BCM 番号 |
| キーボード | ダッシュボードを開いた状態で **スペースキー** | 不要 |
| USB ペダル | **F13〜F24** を送るペダル。踏んでいる間が録音 | 不要 |

```yaml
ptt:
  gpio: 17      # BCM番号。GPIOがない環境では '' のまま
```

キーボードとペダルはダッシュボードの画面上で効きます。押下直後に画面が「録音中」へ変わり、離すと送信します。読み上げ中に押すと、物理PTTと同じく読み上げをキャンセルして録音を開始します。テキスト入力欄にカーソルがあるときは通常のスペース入力を優先して録音を始めません。

GPIO スイッチには押し方による操作があります。

| 操作 | 動作 |
| --- | --- |
| 押している間 | 録音 |
| 離す | 録音を止めて、文字起こしと応答へ進む |
| 短押し 1 回 | 録音を取り消す |
| 短押し 2 回 | 次の会話スロットへ切り替える |
| 処理中に短押し | 読み上げを止めて、録音は取り消す |
| 処理中に押し続ける | 読み上げを止めて、そのまま録音を始める |

本人確認でロックされている間は、短い合言葉を取りこぼさないよう短押しも録音として扱います。ただし短押し 2 回はロック中でもスロット切り替えを優先します。

### マイクとスピーカーを選ぶ

`/settings` の「マイク・スピーカー」から、接続済みの候補を選べます。設定ファイルで指定する場合はこう書きます。

```yaml
audio:
  input_devices:                      # 上から順に、接続済みのものを使う
    - default
    - plughw:CARD=USBMic,DEV=0
  output_device: default
  output_volume: 70
```

複数のマイクを使い分ける場合は `input_devices` に候補を並べます。録音開始時に接続済みのカードを選びます。

### マイクを止める

画面のマイク OFF ボタンで、呼びかけと PTT の受け付けを一時停止できます。もう一度押すと再開します。

### 長い発話を受け付ける

ウェイクワード方式では、無音またはVADで発話終了を検出します。終了を検出できない場合の最長録音時間は次の設定です。既定は60秒です。

```yaml
wakeword:
  record_max_seconds: 60
```

### 長時間の応答を待つ

同じホストのAgent Runnerは時間制限なく完了を待ちます。別のARGOSへ接続するスロットは、次の秒数まで無通信状態を待ちます。既定は30分で、0なら無制限です。

```yaml
remote_argos:
  timeout_seconds: 1800
```

Web検索の回答は、内部引用文字を表示せず、取得できた出典を回答末尾のリンクとして表示します。出典一覧とURLは読み上げません。

### 誤検知が多いとき

呼びかけの誤検知が多い場合は、感度を下げるか、文字起こし結果が呼びかけで始まるときだけ処理する設定にします。

```yaml
wakeword:
  threshold: 0.7                  # 上げるほど鳴りにくい
  require_stt_wakeword: true      # 文字起こしが呼びかけで始まるときだけ処理
  false_positive_capture: true    # 誤検知の録音を保存する
  false_positive_dir: /tmp/argos/wakeword-candidates
```

保存された録音を聞いて誤検知だと確認できたものだけ、学習用データへ移してください。既定の保存先は `/tmp` 配下なので、再起動で消えます。必要なものは先に退避します。

---

## 3. 画面で使う

ブラウザで `http://<ホスト>:8765/` を開きます。画面は 4 種類あります。

| URL | 用途 |
| --- | --- |
| `/` | 標準レイアウト。横長ディスプレイ向け |
| `/grid` | タイルレイアウト。複数スロットを並べて見る |
| `/sp` | スマートフォン・タブレット向け |
| `/settings` | ARGOS 本体の設定 |

`/` を開いたときにどのレイアウトを出すかは、前回の選択（ブラウザに保存）と既定値で決まります。

**設定**

```yaml
dashboard:
  enabled: true
  host: 127.0.0.1          # 他の端末から見るなら 0.0.0.0
  port: 8765
  token: ''                # 空ならインストーラーが自動生成
  view_key: ''             # 閲覧用のアクセスキー
  screensaver_seconds: 1800
  default_font_size: medium
```

### 標準レイアウト

左に状態・現在のスロット・Wi-Fi・利用枠、中央に会話、右に通知が並びます。画面幅に応じて列の数と通知欄の位置が変わります。

- 中央と右のパネルはタッチで縦にスクロールできます
- 会話は末尾を見ているときだけ新着に追従します。過去ログを読んでいる間は表示位置が動きません
- 左パネルの `CURRENT SLOT` をタップすると全スロットの一覧が開きます
- 表示していないスロットの答えは読み上げず、そのスロットに切り替えたときに読み上げます。裏で完了したスロットは未読として表示されます
- ブラウザで回答を読み上げている途中にスペースキーまたはUSBペダルを押すと、音声だけを停止して次の録音を始めます。回答の文章は最後まで画面へ表示されます
- 音声形式やブラウザの出力に問題がある場合は、入力欄の上へ「回答音声を再生できません」と理由を表示します

### タイルレイアウト

複数の会話スロットをタイルで並べて、同時に眺められる画面です。タイルからも音声入力とミュート操作ができます。

フォーカス中のタイルで読み上げている途中にスペースキーまたはUSBペダルを押すと、音声だけを停止して録音へ切り替えます。途中まで届いている回答文は消さず、残りも最後まで表示します。

タイルを大きく見たいときは、**タイル右上の最大化ボタン（⛶）**を押します。PC ではタイルをダブルクリックしても最大化できます。もう一度押す（またはダブルクリックする）と元に戻り、スクロール位置も保たれます。

画面右上には次のボタンが並びます。

| ボタン | 動作 |
| --- | --- |
| ⚙️ 設定 | 標準レイアウトへ移り、設定モーダルを開く |
| Usage | エージェントの利用枠を表示する |
| ミュート | 読み上げを一時停止する。通常・SP画面とも状態を共有する |

設定モーダルの中身は「[画面まわりの設定](#画面まわりの設定)」と同じです。ここから ARGOS 本体の設定（`/settings`）へも進めます。

### スマートフォン表示

`/sp` は本文をスクロールさせない専用表示です。左上のボタンで状態とスロット、右上のベルで通知を開閉します。通知バッジは未確認の件数を示し、通知欄を開くと端末ごとに確認済みになります。

HTTPS で開いた場合は、入力欄横のマイクボタンで録音できます。もう一度タップすると送信し、答えは同じブラウザで再生されます。初回はブラウザのマイク利用許可が必要です。

### テキストで話しかける

会話欄の下にある入力欄に文字を入れて送ると、現在のスロットへ同じ会話の続きとして渡せます。この操作では文字起こしも読み上げも使わず、答えは文字で表示されます。回答の処理中でも、別の空いているスロットへ切り替えてテキストで会話を続けられます。

### 音量とミュート

ロゴの下のミュートボタンで読み上げを一時停止します。ミュート中は再生中の音声を止め、解除するとキューに残っている読み上げから再開します。読み上げ音量は縦スライダーで変えられます。

音量とミュートの状態は保存され、ARGOS を再起動しても復元されます。ミュート状態は通常・SP・Gridの各画面で共通です。

### 画面保護

一定時間操作がないと黒い全画面表示に切り替わります。タッチのほか、PTT の録音開始でも解除されます。

```yaml
dashboard:
  screensaver_seconds: 1800    # 0以下で無効
```

### 画面まわりの設定

ダッシュボード右上のメニュー（三本線）を開きます。**このブラウザにだけ**効きます。

| 項目 | 内容 |
| --- | --- |
| 初期表示デフォルト | `/` を開いたときのレイアウト（通常 / SP / Grid） |
| 画面配置 | 左右の入れ替え |
| 表示フォントサイズ | `小` `中` `大` |
| 会話セッション | セッションリセット、履歴を引き継いで新規セッション |
| ARGOS 本体 | 本体設定（`/settings`）を開く |

### ARGOS 本体の設定画面

`/settings` は **`config.yaml` の全項目**を画面から編集できる入口です。ファイルを直接開かずに設定を変えられます。項目は設定ファイルの区分ごとにまとめて表示されます。

| 区分 | 例 |
| --- | --- |
| マイク・スピーカー | 入出力デバイス、音量、待ち受け方法 |
| ウェイクワード | 有効・無効、検出感度、割り込み |
| PTT ボタン | GPIO 番号 |
| エージェント共通 / AI プロバイダー | スロット、処理中の声かけ、provider ごとの起動オプション |
| ダッシュボード | 待ち受けアドレス、トークン、画面保護 |
| 本人確認 / 会話履歴 / 会話メモリー | 各機能の有効・無効と保存先 |
| GPS・地図 / 小型 LCD / 起動演出 | 車載まわり |
| 音声認識 API / VOICEVOX / 読み上げ前処理 | 外部サービスの接続先 |

マイクとスピーカーのデバイスは、接続済みの候補から選べます。

**保存しても、すぐには反映されません。** 保存時に「反映にはARGOSの再起動が必要です。」と表示されるので、再起動してください。

```bash
sudo systemctl restart argos.service
```

保存の前に `config.yaml` のバックアップを自動で作ります（`config.yaml.backup-<日時>`）。設定を壊したときはこのファイルから戻せます。

---

## 4. エージェントと会話スロット

### スロットを設定する

ARGOS は複数の**会話スロット**を持てます。スロットごとに、使うエージェント、作業ディレクトリ、モデル、読み上げの声を分けられます。

```yaml
agent:
  provider: codex          # 既定のprovider
  slots:
    - type: local
      name: Codex          # 画面と読み上げに出る名前
      provider: codex      # codex / claude / antigravity / hermes
      cwd: /opt/argos      # エージェントの作業ディレクトリ
      voicevox_speaker: 2  # このスロットの声
      model: ''            # 空ならCLIの既定
      ptt_cycle: true      # PTT短押し2回の巡回対象にするか
    - type: local
      name: 調査
      provider: claude
      cwd: /home/argos
      voicevox_speaker: 8
```

書いた順が、画面の並び順と PTT 短押し 2 回の巡回順になります。`ptt_cycle: false` にしたスロットは画面から選べますが、短押し 2 回では回ってきません。

### 別ホストの ARGOS をスロットにする

自宅の ARGOS を出先から使う、といった構成ができます。

```yaml
agent:
  slots:
    - type: remote
      name: 自宅Codex
      url: https://home.example.ts.net
      token: ''                  # 接続先のdashboard.token
      remote_name: 作業          # 接続先のスロット名
      remote_provider: codex
      voicevox_speaker: 2
      ptt_cycle: false
```

### スロットを切り替える

画面の `CURRENT SLOT` から選ぶか、PTT の短押し 2 回です。切り替えると音声で名前を読み上げます。

### 共通の指示を渡す

会話の開始時にだけ、ARGOS 共通の指示をエージェントへ渡します。追加したい指示があれば設定します。

```yaml
agent:
  system_prompt: '簡潔に答えて。'
  system_prompt_file: ''         # ファイルから読むならこちら
  skills_dir: /opt/argos/skills  # スキルの置き場
```

`skills_dir` に置いた `SKILL.md` は、依頼に応じてエージェントが読みに行きます。

### 処理中の声かけ

エージェントを呼んだ直後と、応答が遅いときに短い声かけをします。

```yaml
agent:
  progress_voice: true
  progress_first_delay_seconds: 15
  progress_interval_seconds: 30
```

ブラウザから音声入力した場合も同じ声かけを再生します。短い声かけ音声はブラウザごとに初回だけ取得し、同じ文言と話者ではキャッシュを再利用します。

### 利用枠を表示する

JSON を返すコマンドを設定すると、そのエージェントを使っているときだけ利用枠を画面に出します。

```yaml
agent:
  usage_commands:
    codex: cat /opt/argos/services/agent-limit/codex.json
    claude: cat /opt/argos/services/agent-limit/claude.json
```

同梱の更新スクリプトを 5 分おきに動かす cron を、インストーラーが登録します。

---

## 5. 会話を管理する

### 履歴を残す

有効にすると、答えが返るたびにスロット別の履歴を保存し、ARGOS を再起動しても画面に復元します。

```yaml
conversation_history:
  enabled: true
  path: ~/.local/state/argos/conversation-history.json
  max_messages: 100
```

### セッションを作り直す

エージェント側の文脈が長くなったり、話題を変えたいときに使います。

- **セッションリセット** — エージェントとの会話を新しく始めます
- **履歴を引き継いで新規セッション** — いまの履歴をエージェントに要約させてからリセットし、次の入力にその要約を一度だけ添えます。流れを保ったまま軽くできます

いずれもダッシュボード右上のメニューから実行できます。音声やテキストで `/reset` と入力してもリセットできます。

要約の引き継ぎには会話メモリが必要です。

```yaml
conversation_memory:
  enabled: true
  path: ~/.local/state/argos/conversation-memory.json
```

---

## 6. 本人確認

有効にすると、確認が済むまで発話をエージェントへ渡しません。

```yaml
auth:
  enabled: true
  keyword_hash: ''             # 下のコマンドで作る
  trust_seconds: 1800          # 確認済みが続く秒数
  warning_delay_seconds: 10    # 未確認のまま経つと警告
  alert_delay_seconds: 30      # さらに経つと警戒モード
  alert_command: ''            # 失敗が続いたときに実行するコマンド
```

### 合言葉で解除する

合言葉は平文で保存しません。ハッシュ化して `auth.keyword_hash` に設定します。

```bash
uv run scripts/hash-auth-keyword.py
```

文字起こしの揺れを許したい場合は、複数のハッシュをセミコロン区切りで並べます。

ロック中の発話は文字起こしにだけ使い、合言葉が一致すれば解除します。合言葉そのものはエージェントへ送りません。ロック中に PTT を押している間は画面に「本人確認録音中」、離したあとは「本人確認中」と表示され、押下が伝わったか目で確認できます。

### 顔で解除する

カメラを使う場合は、先に顔を登録します。

```bash
uv run scripts/check-face-detection.py       # 顔が1件検出されるか確認
uv run scripts/enroll-face-auth.py --count 5 # 顔を登録
```

`check-face-detection.py` で顔が 1 件だけ検出されることを確認してから登録します。登録後に有効にします。

```yaml
auth:
  face_enabled: true
  face_image_rotation: 0       # カメラが横向きなら 90 など
  face_threshold: 68
```

起動時とロック中の発話時に照合し、成功すればその発話はそのままエージェントへ渡ります。失敗しても合言葉で解除できます。顔検出には OpenCV が必要です（インストーラーが導入します）。

### 未確認のまま放置したとき

起動後に未確認だと「本人確認をしてください。」と案内します。一定時間が過ぎると警告音と案内を繰り返し、さらに時間が経つと「警戒モードに入りました」と案内して画面を警戒中の表示に切り替えます。

案内や警告音は PTT で録音している間は鳴りません。案内の声がマイクへ回り込んで合言葉の認識を邪魔しないためです。

---

## 7. 通知

### 受け取る

外部サービスから送られた通知は、画面右の通知欄に表示されます。画像やリンクを添えることもできます。読み上げや通知音の指定があれば、画面を起こして知らせます。

内部エラー（文字起こし、エージェント、読み上げ、音声再生）も通知欄に赤で表示されます。同じエラーが続いた場合は 1 件にまとめます。

### 送る

`POST /api/events` に JSON を送ります。`dashboard.token` による Bearer 認証が必要です。

```bash
curl -X POST http://<ホスト>:8765/api/events \
  -H "Authorization: Bearer <トークン>" \
  -H "Content-Type: application/json" \
  -d '{"type":"notification","source":"mail","title":"新着メール","text":"確認が必要です"}'
```

`type` には次の値を指定できます。

| type | 用途 |
| --- | --- |
| `notification` | 通知を追加する |
| `user_message` / `agent_message` | 会話欄へ発言を追加する |
| `status` | 状態表示を更新する |
| `clear_notifications` | 通知を消す |
| `overlay` / `clear_overlay` | 画面へ別のページを重ねる／消す |
| `swap_slots` | タイルの並びを入れ替える |
| `clear_center_alert` | 中央アラートを消す |

通知には `image_url`、`link_url`、`sound`、`speak` を添えられます。`display` に `center` を指定すると、画面中央に大きく重ねて表示します。「ご飯だよ〜」のように全員へ強く見せたい連絡向けです。`duration_seconds` を付けると自動で閉じ、付けなければ画面をタップして閉じます。

```bash
curl -X POST http://<ホスト>:8765/api/events \
  -H "Authorization: Bearer <トークン>" \
  -H "Content-Type: application/json" \
  -d '{"type":"notification","source":"kitchen","title":"ご飯だよ〜","display":"center","duration_seconds":30,"speak":true}'
```

画像を添える場合は、先にアップロードして得た URL を `image_url` に指定します。

```bash
url=$(curl -s -X POST http://<ホスト>:8765/api/uploads \
  -H "Authorization: Bearer <トークン>" \
  -H "Content-Type: image/jpeg" --data-binary @photo.jpg \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["url"])')
```

アップロードした画像は保存件数と容量の上限を超えると古いものから消えます。

---

## 8. 離れた場所から使う

ダッシュボードは同じ LAN の別端末からも開けます。

```yaml
dashboard:
  host: 0.0.0.0
  token: '<長いランダム文字列>'
  view_key: '<閲覧用のキー>'
```

**会話履歴を含むため、インターネットへ直接公開しないでください。** 外から使いたい場合は VPN（Tailscale など）を経由してください。

アクセスの制御は 2 種類あります。

| 設定 | 守る範囲 |
| --- | --- |
| `dashboard.view_key` | 画面と状態表示。設定するとキーを持つ人だけが開けます |
| `dashboard.token` | ミュート、音量、通知投稿、テキスト送信などの操作 API |

`token` が空の場合、`argos-install --apply` または `--update` の実行時にランダムな値を自動生成します。同じ値を同梱リマインダーの設定にも反映します。

Web ターミナルを重ねて表示する機能もありますが、シェル操作の権限を持つため外部へ公開せず、ローカル表示に閉じてください。

```bash
uv run scripts/show-ttyd-tmux-overlay.py --target-slot center --replace-top
```

---

## 9. 外部サービスをつなぐ

文字起こしと音声合成は別のサービスに任せます。

```yaml
stt:
  url: http://stt-host:23000
  language: ja
  bearer_token: ''
  use_opus: true          # 送信サイズを減らす。受信側の対応が必要
  opus_bitrate: 24k
voicevox:
  url: http://localhost:50021
  bearer_token: ''
  speaker: 2              # 既定の話者ID
  speed_scale: 1.0
tts:
  url: http://127.0.0.1:9191    # 同梱のtts-filter
  delimiters: 。！？!?           # 読み上げを分割する文字
  cache:
    enabled: true
```

読み上げは区切り文字ごとに分割して順に合成します。`.` は `README.md` のような語を途中で割らないよう既定では区切りに含めません。改行は常に区切りとして扱います。

短い文はキャッシュに保存し、同じ文と話者の組み合わせでは再合成を省きます。

---

## 10. 車載する場合

車に載せるときだけ必要になる設定です。据え置きで使う場合は読み飛ばして構いません。

### PTT スイッチ

ハンドル付近に物理ボタンを置きます。運転中に画面を見ずに操作できるため、車載では PTT が主な入口になります。

```yaml
ptt:
  gpio: 17     # BCM番号
```

### 小型 LCD

ST7789 の小型 LCD をつなぐと、読み上げている文を表示できます。夜間に眩しくならないよう、黒背景に白文字で表示します。

```yaml
lcd:
  enabled: true
  width: 76
  height: 284
  x_offset: 82
  y_offset: 18
  dc_pin: D25
  cs_pin: D5
  reset_pin: D24
  baudrate: 4000000
  font_path: /usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf
  font_size: 16
```

### 現在地

地図の現在地は、この端末の GPS（gpsd または GPS デバイス）から取るか、スマートフォンなど別端末の GPS API から取ります。

```yaml
location:
  provider: local        # local または remote
  remote:
    url: http://localhost:8080/gps
    timeout_seconds: 2
```

`/settings` の「GPS・地図」からも切り替えられます（保存後に再起動が必要です）。

### HDMI ディスプレイへ全画面表示

車載ディスプレイへキオスク表示します。

```bash
./scripts/open-dashboard-kiosk.sh              # その場で全画面表示
./scripts/install-dashboard-autostart.sh       # 起動時に自動表示
```

自動表示の状態は `systemctl --user status argos-dashboard-kiosk.service` で確認できます。専用の Chromium プロフィールを使い、翻訳 UI や同期 UI、マウスカーソルは表示しません。

### 起動時の演出

```yaml
startup:
  splash_enabled: true
  splash_seconds: 3
  sound_enabled: true
```

### 走行中の注意

- 読み上げ中の割り込みを使う場合は、必ずエコーキャンセルを設定してください（[2 章](#読み上げ中に割り込む)）
- 車内ノイズで誤検知する場合は感度を上げてください（[2 章](#誤検知が多いとき)）
- 画面保護を短くしすぎると、走行中に画面が消えやすくなります

---

## 11. 困ったとき

### まず状態を見る

```bash
systemctl status argos.service
journalctl -u argos.service -f
```

エージェントを別プロセスで動かしている場合は、そちらのログも見ます。

```bash
journalctl -u argos-agent-runner.service -f
```

### よくある症状

| 症状 | 確認すること |
| --- | --- |
| 呼びかけに反応しない | マイク OFF になっていないか。`/settings` でマイクのデバイス選択が合っているか |
| 呼びかけていないのに反応する | `wakeword.threshold` を上げる。`require_stt_wakeword` を有効にする |
| 話しかけても答えが返らない | 本人確認でロックされていないか。画面の状態表示を見る |
| 答えが読み上げられない | ミュートになっていないか。音量が 0 になっていないか。VOICEVOX が動いているか |
| 文字起こしされない | `stt.url` のサービスが動いているか。`bearer_token` が合っているか |
| 画面が真っ黒 | 画面保護。タッチするか PTT を押す |
| 画面が開けない | `dashboard.view_key` を設定した場合、キーが必要です |
| エージェントが応答しない | CLI のログイン期限が切れていないか。ARGOS 実行ユーザーで CLI を直接起動して確認する |
| 設定を変えても効かない | `config.yaml` の編集も `/settings` からの保存も再起動が必要です |

### 設定を反映する

```bash
sudo systemctl restart argos.service
```

`config.yaml` の編集も `/settings` からの保存も、反映には再起動が必要です。再起動なしで変わるのは、ダッシュボードのメニューにある画面まわりの設定（レイアウト、文字サイズ、左右入れ替え）と、ミュート・音量・マイク OFF の操作だけです。
