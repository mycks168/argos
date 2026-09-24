---
name: argos-screen-layout
description: Waydroidとlabwcがある端末専用。ARGOSのダッシュボードとWaydroid上のGoogleマップの画面配置を切り替える（地図を出す・隠す、ARGOSだけ表示、地図を最大化、左右入れ替え、会話欄を右ペインへ移す）。ユーザーが「地図を出して」「地図を隠して」「地図を消して」「ARGOSだけ見せて」「地図を最大にして」「左右を入れ替えて」「会話を右に移して」「メッセージが見えない」などと言ったときに使う。
---

# ARGOS画面配置 (argos-screen-layout)

HDMI画面に、ARGOSダッシュボードとWaydroidのGoogleマップを並べる（左右分割）か、ダッシュボードの中央ペインへ重ねる（オーバーレイ）ためのスキルです。画面サイズは自動で取得します（この実機は1920×440）。地図の中身（ナビ開始・ズーム・GPS追従）は `waydroid-google-maps` スキルの担当で、このスキルは「窓の置き方」だけを扱います。仕組みと実機での確認結果は `docs/waydroid.md` にあります。

## 使える端末

- labwc（Wayland）とWaydroidがある端末だけ。設定 `window_layout.android_app: maps`（config.yaml）が有効なこと。
- 確認済みのアプリはGoogleマップ（`maps`）だけ。それ以外の端末では、`boot` は何もせず正常終了し、他のコマンドはエラーになる。
- Waydroidは1つの画面に1つのAndroidしか出せない（複数ウィンドウ設定は枠が付いて使えなかった）。別のAndroidアプリを並べる場合は、地図と入れ替える形になる。
- ダッシュボードは「通常」レイアウト（3分割）が前提。SP表示・Grid表示では、オーバーレイ（`pane`）の中央ペインの計算が合わない。
- 画面は、有効な出力が1つで、拡大率1.0、回転なしの構成が前提（違うとエラー）。`--width` と `--height`（両方）で画面サイズを手動指定できる。
- 設定は `config.yaml` の `window_layout`（`android_app`・`style`・`split_ratio`・`panel_height`・`restart_services`）。

## コマンド

すべて `/opt/argos` で実行する。サービスやSSHから実行するときは `XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0` を付ける（rootでは実行しない）。実行結果はJSONで返る。

```bash
cd /opt/argos
# 地図をダッシュボードの中央ペインへ重ねる（主な使い方）
uv run python -m argos.tools.window_layout pane
# 地図を隠してARGOSだけ全画面にする（直前の配置を覚える）
uv run python -m argos.tools.window_layout hide
# 隠す前の配置へ戻す（隠していなければ今の配置を再適用）
uv run python -m argos.tools.window_layout show
# 左右半分に分ける／入れ替え／地図だけ全画面／保存した配置へ戻す
uv run python -m argos.tools.window_layout split --ratio 50 --side left
uv run python -m argos.tools.window_layout swap
uv run python -m argos.tools.window_layout android
uv run python -m argos.tools.window_layout restore
# 保存している状態を見る（実際のウィンドウ状態ではない）
uv run python -m argos.tools.window_layout status
```

依頼との対応:

| 依頼 | コマンド |
| --- | --- |
| 地図を出して | `show`（初回や配置が不明なら、設定の表示方式のコマンド） |
| 地図を隠して・消して・ARGOSだけ | `hide` |
| 左右を入れ替えて | `swap`（左右分割のとき） |
| 地図を最大にして | `android`（Androidの描画サイズが画面と同じでないと黒い余白が出る） |
| 3分の1・半分にして | `split --ratio 33` ／ `split --ratio 50`（`--side left|right` で側を選ぶ） |
| オーバーレイにして・重ねて | `pane` |

設定 `window_layout.style`（`overlay` か `split`）が、最初に使う表示方式になる。`restore` はその表示方式の配置へ戻す。

## Androidの描画サイズと再起動

**Waydroidの窓はAndroidの描画サイズより小さくできない**ので、窓の大きさとAndroidの描画サイズ（`persist.waydroid.width`・`height`）を揃える必要がある。コマンドは、モードごとに必要な描画サイズを計算し、出力に次を返す。

- `android_needed`: 必要な描画サイズ（例: 半分の分割は960×404、オーバーレイは1004×416）
- `android_current`: 今の描画サイズ
- `restart_required`: 違うとき `true`。配置だけ行うので、はみ出したり余白が出たりする
- `android_fit`: ぴったり収まっているか

描画サイズは**再起動しないと変わらない**。`--restart-android` を付けると、サイズを変えてWaydroidを再起動し、地図を起動し直してから配置する。**ナビが中断されるので、必ず利用者に確認してから付ける。**

```bash
uv run python -m argos.tools.window_layout split --ratio 50 --restart-android
```

コンテナの再起動にパスワードなしの `sudo` が必要。GPS中継など、再起動の前後で止めて再開するユーザーサービスは `config.yaml` の `window_layout.restart_services` に書く（この実機は `waydroid-gps-bridge.service`）。

Androidの描画サイズを変えても、別の表示方式へ戻すときは再度サイズが変わる（再起動）。頻繁に切り替える運用なら、片方の表示方式に絞る。

## 会話欄を右のペインへ移す

地図は中央ペインに重ねるので、中央にある欄は隠れる。会話が読めなくなったら、ダッシュボードの中央と右を入れ替える。

```bash
uv run python skills/dashboard-overlay/scripts/send_overlay.py --type swap
```

入れ替えた結果は `GET /api/state`（閲覧キーがあれば `?key=`）の `slot_stacks` で分かる。会話が `right` にあれば読める。この入れ替えはダッシュボードの状態で、ARGOS本体の再起動で元に戻ることがある。実行のたびに反転するので、先に `slot_stacks` を確認してから実行する。

## 実行のたびに自動で行われること

- Waydroidのコンテナが凍結（FROZEN）していれば解除し、地図のウィンドウが無ければ起動して、出るまで確認する（最大3回）。`hide` では行わない。
- `pane` のあいだ、上のバー（`wf-panel-pi`）を止める。バーが確保する約36pxのせいで、地図を最上端に置けず下が切れるため。`hide` や他の配置へ切り替えると、バーを再開する（止めたのはこのツールのときだけ）。
- `pane` の地図は最前面に固定する。ARGOS側をタッチして前面に出ても、地図は隠れない。

## はまりどころ（自分で操作するとき）

- **凍結**: Waydroidは既定でコンテナを凍結する。凍結中はウィンドウが消え、`lxc-attach` も応答しない。`waydroid app launch com.google.android.apps.maps` で解除される（sudo不要）。
- **最前面の切り替え**: labwcの `ToggleAlwaysOnTop/Bottom` はフォーカス中の窓に効き、現在の層から切り替わるだけ。自作する場合は、先に `Focus` してから、どの層から始めても結果が決まる並び（最前面: 最背面, 最背面, 最前面 ／ 通常: 最背面, 最前面, 最前面）を使う。
- **タイル状態**: `rc.xml` の `windowRule`（`SnapToEdge`）で並んだ窓は、`UnSnap` しないと移動できない。
- **`lwrespawn`**: バーは `lwrespawn` が再起動するので、`pkill wf-panel-pi` だけでは止まらない。見張り役を先に止める。
- **タッチ**: labwcの `<touch mouseEmulation="yes">` だと指1本にしかならず、地図でピンチできない。`no` にする。
- **`wlrctl`**: `wlrctl toplevel minimize/focus/find` が使えるが、最小化を解除するコマンドは無い（`focus` で戻る）。labwcの `ForEach` を自作するときは、ウィンドウの指定に `app_id:waydroid.com.google.android.apps.maps`（地図）と `title:ARGOS Dashboard`（ダッシュボード）を使う。

## 確認方法

画面を撮って、目で確かめる。

```bash
XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 grim /tmp/screen.png
```

`wlrctl toplevel list` で、`waydroid.com.google.android.apps.maps` と `ARGOS Dashboard` のウィンドウがあるかも分かる。地図が無いときは、まず `waydroid status` で凍結していないかを見る。

## 音声応答のルール

- 短く、友達に話すような日本語で伝える。
- 地図を出す・隠すときは、「ナビは続いているよ」など、ナビの状態を一言で伝える（隠しても、Waydroidは動いたまま）。
- 走行中は、画面の確認が必要な操作を勧めない。
