# Waydroidを使う場合

ARGOSとAndroidアプリを同じLinux端末で使いたい人向けのガイドです。GoogleマップによるナビとARGOSの音声操作を併用する構成を扱います。

## ARGOS本体との関係と対応環境

ARGOSはWaydroidを必須としません。WaydroidはAndroidアプリを利用したい場合に追加する任意の構成です。

Waydroidはラズパイ専用ではなく、Waylandを使う一般のLinuxでも動作する仕組みです。ただし、対応カーネルやグラフィックス環境などの条件があります。導入可否は[公式インストール案内](https://docs.waydro.id/usage/install-on-desktops)と[カーネル要件の確認](https://docs.waydro.id/debugging/getting-essential-information)を参照してください。ARGOSが動く環境すべてで、Waydroidも動くという意味ではありません。

以下の実機記録は2026-09-23のRaspberry Pi 5、labwc、1920×440のタッチ画面を対象に、2026-09-24に整理したものです。他のLinuxデスクトップや解像度で同じ配置を保証するものではなく、導入・再起動・音声入力までの検証を完了した標準構成でもありません。

## Google PlayとGoogleマップ

実機ではGoogle Play経由のGoogleマップを利用しました。Googleアプリを利用するためのAndroidイメージやアカウント設定はWaydroid側の準備です。ARGOSのインストールだけでGoogle PlayやGoogleマップが使えるわけではありません。[Waydroid公式ドキュメント](https://docs.waydro.id/)を参照して準備します。

## ダッシュボードと地図を並べる

実機では左側960×440にGoogleマップ、右側にARGOSを配置しました。ARGOSは枠のないChromiumアプリモードで開き、labwc側のウィンドウルールで配置しています。設定例は[ユーザーマニュアルの自動表示](user_manual.md)にあります。

一般のLinuxでは、画面サイズ、拡大率、OSパネルの占有領域、ウィンドウマネージャーが異なります。この実機の960×440という値やlabwcのルールをそのまま適用せず、使用環境の画面配置に合わせて調整します。ARGOSのSP表示は狭い幅向けの既存レイアウトとして選択できますが、Android側の配置やタッチ座標も別途確認が必要です。

### 画面配置コマンド（実験段階・labwc専用）

`argos.tools.window_layout` は、labwc上の地図（Androidアプリ。この実機ではGoogleマップ）と、タイトルが `ARGOS Dashboard` のChromiumウィンドウの配置を切り替えるコマンドです。ARGOS本体の設定画面や音声操作にはまだ接続していません（ダッシュボードのAPIは、会話欄の入れ替えとレイアウトの切り替えにだけ使います）。labwc専用なので、他のLinuxデスクトップでは使用しないでください。Waydroidは1つの画面に1つのAndroidしか出せないため、Androidの別アプリを二つ同時に並べる機能ではありません。

必要なパッケージは `labwc`、`wtype`、`wlrctl` です。デスクトップへログインしているユーザーのWaylandセッションから、リポジトリ内で実行します。サービスやSSH経由では、そのセッションの `XDG_RUNTIME_DIR` と `WAYLAND_DISPLAY` を明示してください。rootとしては実行しません。

```bash
# Androidを左半分、ARGOSを右半分へ配置（比率は20〜80）
uv run python -m argos.tools.window_layout split --ratio 50 --side left
# 3分の1をAndroidに（右側）
uv run python -m argos.tools.window_layout split --ratio 33 --side right
# 左右を入れ替え
uv run python -m argos.tools.window_layout swap
# ARGOSを全画面表示
uv run python -m argos.tools.window_layout argos
# 設定の表示方式（overlay/split）の配置へ戻す
uv run python -m argos.tools.window_layout restore
# 保存した要求状態を確認（実際のウィンドウ状態の取得ではない）
uv run python -m argos.tools.window_layout status
# 保存済みの状態を再適用（再起動後の復帰用）
uv run python -m argos.tools.window_layout boot
```

#### 表示方式とAndroidの大きさ

表示方式は2つあり、`config.yaml` の `window_layout.style` で最初に使うものを決めます（実行のたびに `pane` や `split` で切り替えられます）。

| 表示方式 | コマンド | Androidの窓 | ARGOS |
| --- | --- | --- | --- |
| `overlay` | `pane` | 中央ペインの位置（下の節） | 全画面 |
| `split` | `split` | 画面の指定割合（`--ratio`）を左右どちらかに | 残り |

**Waydroidの窓はAndroidの描画サイズより小さくできない**ため、窓の大きさとAndroidの描画サイズ（`persist.waydroid.width`・`height`）は揃える必要があります。ツールは、モードごとに必要な描画サイズを計算して、今の値と比べます。

- 同じなら、そのまま配置します。
- 違うと、出力の `restart_required` が `true` になり、`android_needed`（必要なサイズ）と `android_current`（今のサイズ）を表示して、配置だけ行います（はみ出したり余白が出たりします）。
- `--restart-android` を付けると、描画サイズを変えてWaydroidを再起動し、地図を起動し直してから配置します。**ナビなどAndroid側の処理は中断されます。** コンテナの再起動に、パスワードなしの `sudo` が必要です。`config.yaml` の `window_layout.restart_services` にユーザーサービス（この実機はGPS中継 `waydroid-gps-bridge.service`）を書くと、再起動の前後で止めて再開します。

例（この実機・1920×440、上のパネル36px）:

| モード | Androidの窓 | 必要な描画サイズ |
| --- | --- | --- |
| `pane`（overlay） | 中央ペイン | 1004×416 |
| `split --ratio 50` | 左半分 | 960×404 |
| `split --ratio 33` | 左または右の約3分の1 | 634×404 |
| `android`（全画面） | 画面全体 | 1920×440 |

```bash
# 半分の分割へ切り替える（描画サイズが合わなければ再起動する）
uv run python -m argos.tools.window_layout split --ratio 50 --restart-android
```

#### Androidの見せ方（app／full）

`window_layout.android_view` で、Androidの見せ方を選びます。

- **`app`（既定）**: アプリごとに、別のウィンドウを出します（`waydroid app launch`）。ウィンドウの名前は `waydroid.<パッケージ名>` です。アプリが閉じるとウィンドウが消え、Waydroidが凍結するので、見張りが出し直します。別のアプリを起動すると、そのアプリが別のウィンドウで出ます（重なります）。
- **`full`**: Android全体を、1つのウィンドウで出します（`waydroid show-full-ui`）。ウィンドウの名前は `Waydroid` の1つで、Androidの中で、スマホと同じように、アプリが切り替わります。ウィンドウは常にあるので、アプリが閉じてもウィンドウが消えず、凍結もしにくくなります。上のステータスバーと、下の戻る・ホームのボタンが、ウィンドウの中に出るため、アプリに使える高さは、その分、減ります。
- **切り替え**: `config.yaml` に書いて、`window_layout show` などを実行します。`full` では、ウィンドウを出したあと、設定したアプリをAndroidの中で前面に出します。地図の操作スキルも、凍結の解除に `waydroid show-full-ui` を使い、`waydroid app launch` でアプリ表示へ戻ってしまうことを防ぎます。
- **注意**: 音声の部品が壊れる問題（上の「音声の部品が壊れたときの自動復旧」）の原因が、ウィンドウの方式にあるという証拠は、ありません。`full` は、ウィンドウが消える・凍結する問題への対策で、試している段階です。

#### 画面サイズと設定

- **画面サイズ**: 指定がなければ、`wlr-randr --json` で有効な出力の現在のモードから、幅と高さを自動取得します。`--width` と `--height`（両方必要）で上書きできます。有効な画面が複数ある、拡大率が1.0でない、回転している構成は、未対応としてエラーにします。
- **パネルの高さ**: 分割のとき、上のパネル（`wf-panel-pi`）の下から窓を置きます。高さは `window_layout.panel_height`（既定36）で、パネルがない端末では0です。
- **設定項目**（`config.yaml` の `window_layout`）: `android_app`（並べるアプリ）、`android_view`（app/full）、`style`（overlay/split）、`split_ratio`（分割の割合。既定50）、`panel_height`、`restart_services`、`dashboard_layout`、`swap_conversation`。空の値は既定値になります。

#### ダッシュボードのレイアウトの自動切り替え

分割のときはARGOSの幅が狭くなるため、ダッシュボードを狭い幅向けのSP表示にします。重ねる表示（overlay）では、中央ペインの計算が通常レイアウトを前提にしているため、通常表示へ戻します。

- **仕組み**: `window_layout` が、キオスクの起動スクリプトが読むファイル（`~/.local/state/argos/dashboard-layout`）へレイアウト名を書き、**変わったときだけ**キオスク（`argos-dashboard-kiosk`）を再起動します。キオスクは `/?layout=sp` のようにURLで指定して開くので、ブラウザに残っているCookieの設定（設定画面で選んだレイアウト）より優先されます。再起動のあと、ARGOSの窓が出るのを待ってから配置します。
- **対象のモード**: `split` はSP、`pane` は通常です。`hide`（ARGOSだけ）や `android` は今のレイアウトのままで、キオスクも再起動しません（隠す・出すを繰り返しても、画面が乱れません）。
- **固定する**: `window_layout.dashboard_layout` に `standard`・`sp`・`grid` を書くと、モードによらずそれを使います。既定（空または `auto`）は上の自動切り替えです。
- **サーバー**: `GET /?layout=standard|sp|grid` を受け付けます（不明な値は無視して、Cookieと既定値に従います）。`grid` は `/grid` へ転送します。環境変数 `ARGOS_DASHBOARD_KIOSK_LAYOUT` でもキオスクに指定できます（最優先）。
- **キオスクがない端末**: キオスクをこのユーザーが動かしていない端末では、何もしません。
- **切り替え中**: キオスクの再起動で、ARGOSの画面が数秒消えます。

#### 並べるAndroidアプリの設定と凍結対策

並べるAndroidアプリは端末ごとの設定で、初期値は未設定です。`config.yaml` の `window_layout.android_app` に登録済みの名前を書きます（空でない環境変数 `ARGOS_WINDOW_LAYOUT_ANDROID_APP` があればそちらを優先）。

```yaml
window_layout:
  android_app: maps
```

実機で動作を確認したのは `maps`（Googleマップ、`com.google.android.apps.maps`）だけです。パッケージ名の自由指定はできず、登録のない名前はエラーにします。別のアプリを増やすときは、ウィンドウの出現・全画面・分割を実機で確認してから `argos.tools.window_layout.ANDROID_APPS` とインストーラーの質問へ追加します。

未設定の端末では地図を起動しません。`split`・`swap`・`android` は「Androidアプリが未設定」として拒否し、`argos` だけ使えます。設定はインストーラーの対話設定（`--configure`）でも選べます。`window-layout.json` には比率・左右・モードだけを保存します。

Androidを使う配置（`split`・`swap`・`android`・`restore`・`boot`）では、配置の前に次を自動で行います。

- Waydroidのコンテナが `FROZEN`（凍結）なら解除する。Waydroidは既定（`suspend_action = freeze`）でコンテナを凍結し、そのままだとAndroidのウィンドウが消えて配置できないためです。解除は `waydroid app launch` が行うので、sudoは不要です。
- Androidアプリのウィンドウがなければ起動し、ウィンドウが現れるまで確認する。起動成功でもホーム画面だけが出る場合があるため、最大3回まで再試行し、出なければエラーにします。
- `argos`（ARGOSのみ全画面）では、地図の起動や解凍はしません。

#### 再起動後の自動復元

`boot` は保存済みの比率・左右・モードを再適用します。先にAndroid（凍結の解除・地図の起動）を復旧し、そのあとARGOSのウィンドウを最大300秒待ってから配置します。起動直後はダッシュボードの立ち上がりが遅いことがあるためです。失敗したときは、ユーザーサービスが30秒後から自動で再試行します（1時間に5回まで）。前回がARGOS全画面なら地図は起動しません。次の端末では何もせず正常終了するため、起動サービスが有効でも失敗しません。

- Androidアプリが未設定
- labwcのセッションではない（openboxなど）

起動時の実行はユーザーサービス `argos-window-layout` が行います。ARGOSのキオスク（`argos-dashboard-kiosk`）に `PartOf` で紐づけてあり、キオスクが再起動されると配置もやり直されます。**このサービスは任意で、インストーラーは既定では有効化しません。**

- インストーラーの対話設定（`--configure`）で「Waydroidと画面を並べて使う」に `y` と答えると、表示方式（overlay/split）を聞いたうえで、有効化して起動します。`n` なら無効化します。
- 質問しない通常のインストール・更新では、unitファイルを置くだけで、有効・無効の状態は変えません。
- 手動なら `systemctl --user enable --now argos-window-layout` です。
- Waydroid本体や地図の起動サービス（この実機の `waydroid-maps.service` など）は端末固有で、インストーラーは作りません。`After=` などの順序指定は、リポジトリのunitに書かず、端末側のドロップイン（`~/.config/systemd/user/argos-window-layout.service.d/*.conf`）に書きます。ドロップインはインストーラーが上書きしません。

この実機では `waydroid.conf` に `After=waydroid-maps.service` を置いています。

labwcの[ウィンドウ操作](https://labwc.github.io/labwc-actions.5.html)を、一時的な `Super+Ctrl+Alt+F12` の割り当てから実行します。操作完了・通常の例外時には元の `~/.config/labwc/rc.xml` を復元します。既にそのキーが使われている場合は中止します。初回バックアップは `rc.xml.before-argos-layout`、最後に送信した配置は `~/.local/state/argos/window-layout.json` に保存します。再起動後の再適用は `boot` と `argos-window-layout` サービスで行います。

強制終了・電源断では一時設定が残る可能性があります。その場合はバックアップと現在の設定を比較して専用キーだけを取り除き、labwcの設定を再読み込みします。バックアップ以降のユーザー変更を失わないよう、ファイル全体を無条件に上書きしないでください。途中で外部操作に失敗した場合、ウィンドウの位置まで自動で元に戻るわけではありません。

`rc.xml` の `windowRule` に `SnapToEdge` がある端末では、起動し直したウィンドウがタイル状態になり、移動・リサイズが効かなくなります。再起動後に左右入れ替えが効かなかった原因がこれで、配置の前に `UnSnap` でタイルを解除するようにしました（labwc 0.9.8で確認）。

#### ダッシュボードの中央ペインに地図を重ねる（paneモード）

ARGOSを全画面にし、その中央ペインの位置へ地図のウィンドウを重ねます。会話欄を右のペインへ移せば、中央は地図の場所として使えます。

```bash
# 中央ペインの位置と大きさを画面幅から自動計算して配置
uv run python -m argos.tools.window_layout pane
# 地図を隠す（ARGOSだけ）／隠す前の配置へ戻す
uv run python -m argos.tools.window_layout hide
uv run python -m argos.tools.window_layout show
# 範囲を指定（x,y,w,h）／自動計算へ戻す
uv run python -m argos.tools.window_layout pane --pane 435,0,960,440
uv run python -m argos.tools.window_layout pane --pane auto
```

- **中央ペインの計算**: ダッシュボードの3分割（`.dashboard` の `grid-template-columns`）と同じ計算式で、画面幅から求めます。1920×440なら、位置 x=413・y=12、大きさ 1004×416です。x・幅は実機の画面ダンプで測った値と一致します。ダッシュボードのCSSを変えたら、`window_layout.py` の `GRID_WIDE`・`GRID_NARROW` も合わせます。対象は通常レイアウトだけで、SP表示・Grid表示や、列の最小幅に足りない画面幅は未対応です。
- **状態を示す枠**: ダッシュボードの外周には、聞き取り中・処理中・録音中などを色で示す枠（`body::before/after`）が、画面の端から5px内側に幅6pxで描かれます。地図が上下いっぱいまで広がるとこの枠が隠れて、地図だけ浮いて見えるため、上下は `FRAME_MARGIN`（12px）だけ内側に収めます。CSSの枠の位置や幅を変えたら、この値も合わせます。
- **Androidの大きさ**: Androidの描画サイズ（`persist.waydroid.width`・`height`）を読み、ペインより小さければ中央に置きます。**Waydroidの窓はAndroidの描画サイズより小さくできない**ため、ペインより大きいと、窓は縮まず下や右が画面の外へはみ出します（枠も隠れます）。出力の `android_fit` が `true` なら、ぴったり収まっています。再起動せずにAndroidの解像度を変えることはできません（`wm size` は表示が崩れました）。ペインに合わせるには、`persist.waydroid.width`・`height` を `pane_rect` の幅・高さ（この実機は1004×416）にして再起動します。
- **常に手前**: ARGOS側をタッチするとARGOSが前に出て、地図が隠れてしまうため、地図のウィンドウを最前面に固定します。labwcの `ToggleAlwaysOnTop/Bottom` は、`ForEach` で選んだ窓ではなく**フォーカス中の窓**に効き、しかも現在の層から切り替わるだけです。そこで、先に `Focus` で対象へフォーカスを移してから、どの層から始めても結果が決まる操作の並びを使います。paneモード以外へ切り替えるときは、通常の層へ戻します。実機では、地図が最前面に残ること、解除するとARGOSが前に出ることを確認しました。
- **隠す・出す**: `hide` は `argos` モードと同じ（ARGOSだけを全画面にする）で、直前の配置を覚えます。`show` は隠す前の配置へ戻します。隠しても地図は最小化せず、ARGOSの後ろに残ります（Waydroidは動いたまま）。
- **会話欄の自動移動**: 中央ペインは地図の裏になるため、会話欄が中央にあると読めません。`pane` に入るとき、ダッシュボードの状態（`GET /api/state`）を見て、**中央の先頭が会話欄のときだけ**中央と右を入れ替えて、会話欄を右へ移します（`swap_slots` イベントを、Bearer認証付きで送ります）。中央が通知欄や重ねた表示なら、入れ替えません（もう一度入れ替えると、会話欄が隠れるため）。`split`・`hide`・`android` では入れ替えません。ダッシュボードが応答しなくても、配置は成功し、出力の `conversation` に `skipped: …` が入ります。設定 `window_layout.swap_conversation: false` で無効にできます。ダッシュボードの状態はARGOS本体の再起動で初期に戻るので、そのときは `pane` をもう一度実行します。
- **上部のパネル**: Raspberry Pi OSのパネル（`wf-panel-pi`）が上部を確保していると、窓を最上端に置けず、下が切れます。paneモードではパネルを止め、他のモードへ切り替えるときに再開します。止めたのはこのツールだけが対象で、利用者が自分で止めたパネルは再開しません。パネルは `lwrespawn` が自動で再起動するため、先に見張り役を止めます。パネルの停止中は、Wi-Fiなどのアイコンを押せません。
- **再起動後**: `boot` は、保存したモード（pane）と範囲を再適用します。

AIエージェントが地図を出す・隠す・入れ替えるときの手順とはまりどころは、スキル `skills/argos-screen-layout/SKILL.md` にまとめてあります。

#### 実機で分かった制約

> この節は2026-09-24時点の記録です。描画サイズと窓の大きさの連動は、その後、モードごとに必要な描画サイズを計算し、`--restart-android` で合わせる方式で対応しました（上の「表示方式とAndroidの大きさ」）。以下は、その方式を選んだ経緯として残しています。

2026-09-24の画面確認では、左右の入れ替え、ARGOSの全画面化、左右半分への復帰を確認しました。一方、Androidは `persist.waydroid.width=960`、高さ440に固定されています。幅40%の配置を要求してもAndroidの描画解像度まで追従する保証はなく、`android` コマンドで全画面にすると中央に幅960の地図が表示され、左右に黒い余白が残りました。任意比率やAndroidの全画面を完成済み機能とは扱いません。

次の検証はAndroid側の描画解像度とウィンドウサイズの連動です。[Waydroidのプロパティ説明](https://docs.waydro.id/usage/waydroid-prop-options)では、多くの設定にセッション再起動が必要とされています。この検証コマンドはAndroidの設定変更や再起動をしません。ナビ中の中断を避けるため、再起動を伴う試験は別途確認してから行います。物理タッチ位置の一致も未確認です。これらを確認後に、設定画面の比率スライダーと音声操作へ接続する予定です。

#### 再起動を伴う追加検証（2026-09-24）

ユーザーの了承を得て、次の二通りを実機で試しました。

- `persist.waydroid.multi_windows=true` にしてセッションを再起動：GoogleマップはAndroid側の枠付きで細長い表示となり、OS側の全画面操作では画面幅へ広がりませんでした。この構成は採用せず、`false` に戻しました。
- `multi_windows=false`、`persist.waydroid.width=1920`、高さ440で再起動：Googleマップの全画面表示が左右の黒い余白なしになることをスクリーンショットで確認しました。ただし物理タッチ操作とナビ音声の確認は別です。

起動後に幅プロパティだけを768へ変えても、Androidの `wm size` は1920×440のままでした。この試験では、OS側の配置変更だけでAndroidの描画解像度が追従する方法は確認できていません。全画面化そのものは可能ですが、自由な比率変更を再起動なしで行える完成形ではありません。試験後は幅960、高さ440、複数ウィンドウ無効へ戻して再起動し、地図が左半分・ARGOSが右半分に戻ったことをスクリーンショットで確認しました。

また、サービスからの地図起動が成功扱いでも、Androidのホームだけが表示される場合がありました。起動完了後に再度Mapsを起動するとウィンドウが現れました。今後の自動切り替えでは、サービスの終了コードだけで成功とせず、実際のMapsウィンドウの出現まで確認する必要があります。

実装方針は、左右入れ替えやARGOSの全画面化と、Android解像度の変更を分離します。解像度変更で再起動が必要な構成では、ナビが中断する旨を表示し、確定時だけ適用する設計を検討します。スライダーを動かすたびに再起動する処理は入れていません。

## 起動と現在地

### GPS中継の修正（2026-09-24）

この実機の外部プロジェクト `/home/argos/.gemini/antigravity-cli/scratch/waydroid-gps-bridge` を修正しました。ARGOS本体のGPS処理や一般Linux向けの共通設定とは別です。

原因として、(1) Linux側がシリアル受信エラー後に再接続しない、(2) AndroidのAppiumが起動時に見つけたプロバイダだけに配信する、(3) Androidに `gps` プロバイダがなく、Mapsのナビが必要なGPS経路で受信できない、という問題を確認しました。GPSの物理的な受信エラーの発生原因自体は未確定です。

修正内容は次のとおりです。

- GPS・network・fusedの受け口を確認してから配信。GPSがなければ追加し、Appiumに再認識させる。一時的に使うAndroid UID 0のモック位置権限は元へ戻す。
- シリアル切断時に再接続。古い時刻や破損したNMEAを拒否し、有効な測位が5秒途絶えたらAndroid側の繰り返し配信を停止する。Androidコマンド処理中は停止判定が遅れる場合があり、Androidが応答不能な場合の停止は保証しない。
- 中継終了時にも配信を止める。user serviceの `ExecStopPost` にも同じ停止処理を追加。

実機ではGPS受け口の追加とMapsの再起動後、GPSへの受信登録が現れ、「GPSを測位しています」が消え、停車中の速度表示が0km/hになりました。修正版で実際に再発した読み取りエラーも、約2秒後に自動復旧しました。走行中の継続追従とAndroid全体の再起動後の動作は別途確認が必要です。

変更前の中継コード・テスト・README・user serviceは `/home/argos/.local/state/argos/gps-backup.DlF9iK/` に保管しています。詳しい構成、試験、戻し方は外部プロジェクトのREADMEを参照してください。コミットはしていません。

なお、Appium 5.12.0は `accuracy` 指定に対応せず、実際の誤差によらず1メートルを申告します。この点と停車中の測位の揺れは未解決です。また、地図操作スキルの「全体表示でGPS中継を止める」動作は今回変更していません。画面の低電圧警告についても、電源の点検が必要です。

Androidセッションの起動とGoogleマップを画面へ開く処理は別です。実機には地図起動用のユーザーサービス `waydroid-maps.service` を追加しましたが、これは端末固有の設定で、ARGOSの共通インストーラーに組み込んだものではありません。再起動後の表示まで確認してください。

実機ではUSB GPSからAndroidへ位置情報を中継する外部サービスを使っています。ARGOSがGPSを取得できるだけではAndroidにも自動で渡るとは限りません。地図の現在地更新とナビ動作を別々に確認します。

Googleマップへ経路を渡す場合、移動中の座標を固定の出発地として埋め込まず、出発地を省略してGoogleマップ自身の「現在地」を使う方法で表示できました。経由地の表示とナビ開始は別の操作です。

## 音声の併用

以下は前日の実機調査に基づく記録で、現在の設定を再取得したものではありません。

### マイクとPTT

2026-09-24の追加調査では、`config.yaml` の入力が `default` でも、systemdが `.env` から渡す `AUDIO_DEVICE` と `AUDIO_INPUT_DEVICES` が優先され、実プロセスは `plughw:CARD=Microphone,DEV=0` を使用していました。同日09:24のGoogle音声待ち受けの記録に続き09:25から録音のDevice busyが連続していましたが、その瞬間のデバイス所有者までは記録されていません。

ユーザーの了承を得て、実機の `.env` の上記二項目をともに `default` に変更し、ARGOS本体だけを再起動しました。PipeWire経由の短い録音と同時録音2本の成功、起動後の環境変数を確認しました。テスト音声は保存していません。Googleマップで経路表示・ナビ中のPTT入力が成功するかは利用者による確認待ちです。

問題が出た場合の戻し先は `AUDIO_DEVICE=plughw:CARD=Microphone,DEV=0` と `AUDIO_INPUT_DEVICES=plughw:CARD=Microphone,DEV=0;default` です。この二項目だけを戻してARGOS本体を再起動します。ナビやGPS設定はこの試験では変更していません。

PTTではボタンを押すと録音を開始し、離すと停止します。待機中に録音し続けないため、常時入力する方式よりマイクの競合が起きる時間は短くなります。ただし、PTTだから競合しないという保証はありません。

最後に確認した録音ログでは、ARGOSは `arecord -D plughw:CARD=Microphone,DEV=0` でUSBマイクを直接開いていました。同じデバイスをAndroid側や音声サーバーが使用中なら、ARGOSの録音開始が失敗する可能性があります。逆にARGOSが使用中ならAndroid側が利用できない可能性もあります。

#### ナビ中のマイク競合（Device or resource busy）対策（2026-09-24実施）
Googleマップでナビゲーションを開始すると、Googleマップ（およびGoogleアプリ）が音声検索待機のためにマイクを常時オープンし、ARGOS側のPTT録音が `Device or resource busy` で失敗する現象が発生しました。
車載環境では音声対話はARGOS側が一元管理するため、Waydroid側のGoogleマップおよびGoogleアプリからマイク権限（`RECORD_AUDIO`）を無効化（`ignore`）することで競合を完全に解消しました。
```bash
sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- pm revoke com.google.android.apps.maps android.permission.RECORD_AUDIO
sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- cmd appops set com.google.android.apps.maps RECORD_AUDIO ignore
sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- cmd appops set com.google.android.googlequicksearchbox RECORD_AUDIO ignore
```
これにより、ナビゲーション画面の描画や案内音声のスピーカー出力には一切影響を与えることなく、ARGOSが100%確実にマイクを専有して音声認識を行えるようになりました。


### ウェイクワード待機

ウェイクワード方式では待機中もマイク入力を継続的に利用します。直接デバイスを開く構成ではAndroidの音声検索との競合リスクがありますが、ウェイクワード方式自体が使用不可能という意味ではありません。

両方の録音をPipeWireなどの共有可能な入力経路へ統一するか、片方の録音中はもう片方を止める制御を検討します。共有経路での同時録音と、PTT・ウェイクワード・Android音声検索の組み合わせは未検証です。

### スピーカー

前日の作業では `pipewire-alsa` を導入し、ARGOSの出力を `default` に変更しました。Waydroidと同じHDMI出力へ接続する構成を確認しています。出力の共有と入力の共有は別々に確認が必要です。

WaydroidのPipeWireストリームがミュートされていたため解除しました。その後、Androidの通知音・着信音・システム音を0にし、メディア音量を15に設定しました。再起動後の維持は別途確認が必要です。

#### ナビ音声中はARGOSの発話を一時停止する

Googleマップの案内音声とARGOSの声が重なると、どちらも聞き取れません。そこで、Waydroidの音声が鳴っている間は、ARGOSの発話を一時停止し、終わったら続きから再開します（ARGOS本体の機能で、Waydroidの外側で動きます）。

- **検知**: PipeWireのイベント（`pw-dump -m`）から、`application.name` が `Waydroid` の再生ストリーム（`Stream/Output/Audio`）が `running` になったかを見ます。Waydroidは、案内が鳴るたびにストリームを作り、終わると消します。実機の検知は、開始から1秒未満です。
- **保留**: 検知したら、再生中の `aplay` を一時停止（`SIGSTOP`）し、次の音声の再生も再開まで始めません。文の途中でも、その場で止まります。
- **再開**: 再生が止まってから `audio.yield_release_seconds`（既定0.6秒）待って、`SIGCONT` で続きから再生します。案内が続けて鳴るときの短いすき間に、ARGOSが割り込まないための待ち時間です。
- **上限**: AndroidでChromeが音楽を流し続けるなど、ストリームが止まらなくても、ARGOSが黙りっぱなしにならないよう、保留は `audio.yield_max_hold_seconds`（既定20秒）で打ち切り、再開します。その間は声が重なります。
- **中断**: 保留中に発話を中断（`cancel`）したときは、停止中のプロセスへ先に再開の信号を送ってから終了させます。
- **設定**: `config.yaml` の `audio.yield_to_apps` にアプリ名を並べます（空なら何も監視しません）。この実機は `[Waydroid]` です。インストーラーの対話設定で「Waydroidと画面を並べて使う」に `y` と答えると `Waydroid` を追加し、`n` と答えると、この設定が入れた `Waydroid` だけを外します（他のアプリ名は残します）。`pw-dump` がない環境では、監視を無効にして、警告を出します。
- **確認**: 実機で、疑似のWaydroid音声（無音）を流して、保留の開始が0.03秒、再開が音声終了の0.6秒後であること、再生中の `aplay` が停止・再開することを確認しました。Googleマップ本物の案内音声での聞こえ方は、走行中の確認が必要です。
- **限界**: 見分けられるのは、アプリ名までです。Android内のどのアプリの音かは区別できません。また、ナビ音声がマイクに回り込んで、ARGOSに話しかけと誤認される問題は、この機能では防げません。

## Googleマップの音声制御とスキル (waydroid-google-maps)

実機における「横長画面下端のナビ開始ボタンが押しづらい」「ピンチ操作による拡大縮小ができない」「現在地GPS追従によりルート全体表示が勝手にズームされてしまう」という課題に対し、音声制御用スキル `/opt/argos/skills/waydroid-google-maps/` を整備しました。

### 主な制御方式
- **ハンズフリーナビ開始**: 画面の「開始」ボタンをタップする代わりに、Android Intentの `google.navigation:q=<目的地>` を直接発行してターンバイターンナビを即座に開始します。同時にGPS追従サービス（`waydroid-gps-bridge`）を自動で再開します。
- **ルート全体表示時のGPS追従一時停止**: ルート全体や広域を見たい場合は、GPS追従サービスを自動で一時停止し、Googleマップが勝手に現在地に引き戻してズームインするのを防ぎます。
- **音声による縮尺変更（ピンチの代替）**: `geo:0,0?z=<ズームレベル>` を発行することで、画面に触れることなく広域（z=11）や詳細（z=16）への拡大縮小が可能です。
- **CLIスクリプト**: AIエージェントがコマンド一発で操作できるよう、`scripts/map_control.py` を提供しています。
- **凍結の自動解除**: Waydroidのコンテナが凍結中だとIntentが5秒でタイムアウトして失敗するため、実行前に `waydroid app launch` で解除します（実機で凍結状態からの成功を確認）。

## マルチタッチ（ピンチ操作）

Googleマップのピンチイン・ピンチアウトができない場合は、labwcの `~/.config/labwc/rc.xml` にある `<touch ... mouseEmulation="yes">` を確認します。`yes` だとタッチがすべてマウス操作に変換され、指1本の操作しかAndroidへ届きません。

この実機（ILITEKのタッチパネル、10点まで検知）では `mouseEmulation="no"` に変更し、`labwc -r` で反映してピンチ操作が効くことを、利用者が画面で確認しました。ARGOSダッシュボードのタップ・スクロールにも問題はありませんでした。Android側にはマルチタッチ用の入力（`wl_touch_events`）があります。

- 対象はタッチパネルだけで、USBマウスの動作には影響しません（マウスのズームはホイール）。
- インストーラーの対話設定（`--configure`）で「Waydroidと画面を並べて使う」に `y` と答えたときだけ、`rc.xml` の `<touch>` に `mouseEmulation="yes"` があれば `no` に変えます。変更前に `rc.xml.before-argos-touch` を残し、コメントなど他の記述は保ち、labwcへSIGHUPで再読み込みを通知します。`no` や未指定（labwcの既定は変換なし）、`rc.xml` がない端末では何もしません。`n` と答え直しても `yes` には戻しません。
- 戻すときは手動で `yes` に戻して `labwc -r` を実行します。
- 変更前の設定は、この実機の `rc.xml.before-touch-test` に残してあります。
- 画面配置ツール（`window_layout`）は、実行時点の `rc.xml` を保存して復元するため、この設定を保ちます。
- 位置のずれの改善や、走行中の操作性は、別途確認が必要です。

## アプリの通知の読み上げ

Slackなど、Androidアプリの通知を、ARGOSの通知欄に出して読み上げられます（`notice.android`）。ARGOSが5秒ごとに `dumpsys notification` でAndroidの通知の一覧を読み、新しいメッセージだけを知らせます。長い本文は、LAN内のOllamaで要約して読みます。Waydroidが止まっている間や凍結中は、通知が届かないので、読みません。設定と使い方は [利用者マニュアル](user_manual.md) の「Androidのアプリの通知」、仕組みは [基本設計](basic_design.md) を見てください。

## 音声の部品が壊れたときの自動復旧（見張り）

2026-09-26と27の2日続けて、Androidの音声サーバー（`audioserver`）が、ホストのPulseを呼ぶ処理の待ちで5秒以上止まり、Androidの監視（TimeCheck）に強制終了されました。音声HAL（`android.hardware.audio.service`）も一緒に消えて自動では戻らず、地図は音声プレーヤーを解放できずに固まり、起動もできなくなりました（ホストのカーネルログに `Could not ctl.interface_start for 'android.hardware.audio@4.0::IDevicesFactory/default'` が毎秒出続けます）。Waydroidを再起動すると直ります。**固まる直接の原因は特定できていません**（時刻が近い定期処理・周期性は見つからず、CPUとメモリに余裕がある状態でも起きました）。GPS中継（毎秒 `lxc-attach` で位置を送る）、`audio.yield_to_apps` の監視、ARGOSとAndroidが同じHDMI出力を共用していることは、疑わしい点として残しています。

`argos.tools.waydroid_watchdog`（ユーザーサービス `argos-waydroid-watchdog`）が、次の見張りを続けます。

- **検知**: 10秒ごとに、`audioserver` と音声HALが両方あり、かつ音声の中心機能（`service check media.audio_flinger` が `found`）が登録されているかを確認します。2026-09-27の再発では、プロセスは再起動されていても、音声HALを起動できず `audio_flinger` が登録されないまま、地図が経路検索で止まりました。プロセスの有無だけでは、この状態を見逃します。起動から120秒以内、凍結中、停止中、コンテナに入れないときは判断しません。連続2回、欠けていたら「壊れた」と判断します。
- **復旧**: 今の描画サイズのままWaydroidを再起動し（`restart_services` のサービスも止めて再開）、`window_layout show` で画面配置を戻します。**ナビは中断されます**（自動では再開しません）。再起動は、1時間に3回までで、超えたら、記録だけ残して、再起動しません。
- **原因の記録**: 壊れたと判断したとき、`~/.local/state/argos/waydroid-incidents/<日時>/` に、直前の約10分のホストの様子（負荷・空きメモリ・音のストリームの状態。10秒ごと）、Androidのオーディオ関連のログ、最新のtombstone、プロセス一覧、ホストのカーネルログを保存します。次に起きたとき、原因を絞る材料にします。
- **地図のウィンドウが消えたとき**: Googleマップは、ナビを終了すると、ウィンドウごと閉じることがある（アプリの普通の動き）。そのままだと、Waydroidが凍結して、地図がなくなります。配置ツールが地図を出す配置（分割・重ね表示）なのに、地図のウィンドウが、続けて3回（約30秒）見つからなければ、`window_layout show` で出し直し、止まっていた `restart_services` のサービス（GPS中継など）も、再開します。ARGOSだけを出す配置（`hide`）、Androidアプリが未設定、ARGOSの画面も無い、labwcのセッションが無い、のときは判断しません。出し直しは、2分の間隔を空けて、1時間に5回までです。地図の操作スキルの「全体表示」で止めたGPS中継も、地図が閉じたときは、戻ります。
- **導入**: 任意サービスで、インストーラーの対話設定で「Waydroidと画面を並べて使う」に `y` と答えると、画面配置のサービスと一緒に有効化し、`n` なら無効化します。コンテナの再起動とカーネルログの取得に、パスワードなしの `sudo` が必要です。手動なら `systemctl --user enable --now argos-waydroid-watchdog` です。

## 未解決事項

- Waydroid起動とAI応答遅延の因果関係は未確認です。短い返答でも最初の応答まで数分かかった記録がありますが、Waydroidが原因とは断定できません。
- タッチ位置のずれが報告されていますが、Intentによる直接起動と音声ズーム操作によって、運転中の画面タッチ不要化（ハンズフリー化）を達成しました。
- Googleマップの位置精度確認（Location Accuracy）ダイアログは、一度設定をONにすることで次回以降表示されなくなることを確認しました。
