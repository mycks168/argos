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

`argos.tools.window_layout` に配置検証用コマンドを追加しました。ARGOS本体のAPI・設定画面にはまだ接続していません。他のLinuxデスクトップでは使用しないでください。対象はAndroidアプリ（この実機ではGoogleマップ）と、タイトルが `ARGOS Dashboard` のChromiumウィンドウです。Androidの別アプリを二つ並べる機能ではありません。

必要なパッケージは `labwc`、`wtype`、`wlrctl` です。デスクトップへログインしているユーザーのWaylandセッションから、リポジトリ内で実行します。サービスやSSH経由では、そのセッションの `XDG_RUNTIME_DIR` と `WAYLAND_DISPLAY` を明示してください。rootとしては実行しません。

```bash
# Androidを左半分、ARGOSを右半分へ配置
uv run python -m argos.tools.window_layout split --ratio 50 --side left
# 左右を入れ替え
uv run python -m argos.tools.window_layout swap
# ARGOSを全画面表示
uv run python -m argos.tools.window_layout argos
# 保存した比率と左右へ戻す
uv run python -m argos.tools.window_layout restore
# 保存した要求状態を確認（実際のウィンドウ状態の取得ではない）
uv run python -m argos.tools.window_layout status
# 保存済みの状態を再適用（再起動後の復帰用）
uv run python -m argos.tools.window_layout boot
```

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

`boot` は保存済みの比率・左右・モードを再適用します。ARGOSのウィンドウを最大60秒待ってから配置します。前回がARGOS全画面なら地図は起動しません。次の端末では何もせず正常終了するため、起動サービスが有効でも失敗しません。

- Androidアプリが未設定
- labwcのセッションではない（openboxなど）

起動時の実行はユーザーサービス `argos-window-layout` が行います。ARGOSのキオスク（`argos-dashboard-kiosk`）に `PartOf` で紐づけてあり、キオスクが再起動されると配置もやり直されます。**このサービスは任意で、インストーラーは既定では有効化しません。**

- インストーラーの対話設定（`--configure`）で「Waydroidと画面を左右に分割する」に `y` と答えると、有効化して起動します。`n` なら無効化します。
- 質問しない通常のインストール・更新では、unitファイルを置くだけで、有効・無効の状態は変えません。
- 手動なら `systemctl --user enable --now argos-window-layout` です。
- Waydroid本体や地図の起動サービス（この実機の `waydroid-maps.service` など）は端末固有で、インストーラーは作りません。`After=` などの順序指定は、リポジトリのunitに書かず、端末側のドロップイン（`~/.config/systemd/user/argos-window-layout.service.d/*.conf`）に書きます。ドロップインはインストーラーが上書きしません。

この実機では `waydroid.conf` に `After=waydroid-maps.service` を置いています。

labwcの[ウィンドウ操作](https://labwc.github.io/labwc-actions.5.html)を、一時的な `Super+Ctrl+Alt+F12` の割り当てから実行します。操作完了・通常の例外時には元の `~/.config/labwc/rc.xml` を復元します。既にそのキーが使われている場合は中止します。初回バックアップは `rc.xml.before-argos-layout`、最後に送信した配置は `~/.local/state/argos/window-layout.json` に保存します。再起動後の再適用は `boot` と `argos-window-layout` サービスで行います。

強制終了・電源断では一時設定が残る可能性があります。その場合はバックアップと現在の設定を比較して専用キーだけを取り除き、labwcの設定を再読み込みします。バックアップ以降のユーザー変更を失わないよう、ファイル全体を無条件に上書きしないでください。途中で外部操作に失敗した場合、ウィンドウの位置まで自動で元に戻るわけではありません。

`rc.xml` の `windowRule` に `SnapToEdge` がある端末では、起動し直したウィンドウがタイル状態になり、移動・リサイズが効かなくなります。再起動後に左右入れ替えが効かなかった原因がこれで、配置の前に `UnSnap` でタイルを解除するようにしました（labwc 0.9.8で確認）。

#### 実機で分かった制約

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

ARGOSとAndroid間で、発話に合わせて相手の音量を下げる優先制御は、この作業では実装していません。ナビ音声のマイクへの回り込みも確認対象です。

## Googleマップの音声制御とスキル (waydroid-google-maps)

実機における「横長画面下端のナビ開始ボタンが押しづらい」「ピンチ操作による拡大縮小ができない」「現在地GPS追従によりルート全体表示が勝手にズームされてしまう」という課題に対し、音声制御用スキル `/opt/argos/skills/waydroid-google-maps/` を整備しました。

### 主な制御方式
- **ハンズフリーナビ開始**: 画面の「開始」ボタンをタップする代わりに、Android Intentの `google.navigation:q=<目的地>` を直接発行してターンバイターンナビを即座に開始します。同時にGPS追従サービス（`waydroid-gps-bridge`）を自動で再開します。
- **ルート全体表示時のGPS追従一時停止**: ルート全体や広域を見たい場合は、GPS追従サービスを自動で一時停止し、Googleマップが勝手に現在地に引き戻してズームインするのを防ぎます。
- **音声による縮尺変更（ピンチの代替）**: `geo:0,0?z=<ズームレベル>` を発行することで、画面に触れることなく広域（z=11）や詳細（z=16）への拡大縮小が可能です。
- **CLIスクリプト**: AIエージェントがコマンド一発で操作できるよう、`scripts/map_control.py` を提供しています。

## 未解決事項

- Waydroid起動とAI応答遅延の因果関係は未確認です。短い返答でも最初の応答まで数分かかった記録がありますが、Waydroidが原因とは断定できません。
- タッチ位置のずれが報告されていますが、Intentによる直接起動と音声ズーム操作によって、運転中の画面タッチ不要化（ハンズフリー化）を達成しました。
- Googleマップの位置精度確認（Location Accuracy）ダイアログは、一度設定をONにすることで次回以降表示されなくなることを確認しました。
