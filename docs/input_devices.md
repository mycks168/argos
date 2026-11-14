# 入力機器ごとの操作設定

USBノブ・USBキーボード・Bluetoothメディアボタン・Bluetoothキーボードは、
`config.yaml`の`ptt.devices`で操作を設定できます。機器を追加したり、ボタンの
役割を変えたりするためにソースを修正する必要はありません。

## 機器の識別

`id`はLinuxの入力機器が持つベンダーIDと製品ID（16進数）です。
`/sys/class/input/event*/device/id/vendor`と`product`で確認できます。
USBとBluetoothのどちらでも同じ形式を使います。接続で変わるevent番号は設定しません。
同じ製品を2台区別する運用は対象外です。複合機器のキー側が複数のeventに
分かれている場合は、割り当てたキーを持つノードをまとめて監視します。
機器IDの重複登録や不正な設定は、起動時・設定画面の保存時にエラーにします。

## 設定例

```yaml
ptt:
  devices:
    - id: '514c:8850'
      name: 手元のノブ
      preset: media_knob
      exclusive: true
      bindings:
        KEY_VOLUMEUP: {action: volume_up, amount: 3}
        KEY_VOLUMEDOWN: {action: volume_down, amount: 3}
    - id: '1234:5678'  # 実際のキーボードのIDに置き換える
      name: キーボード
      bindings:
        KEY_F13: ptt
        KEY_F14: cancel
```

変更後はARGOS本体を再起動します。機器を抜き差しした場合は自動で探し直します。
`name`は説明用で、機器の照合には使いません。

## 既定の割り当て

- `media_knob`: ミュートキーで録音開始／停止・送信、音量キーで±5、
  曲送り／戻しでスロット切替。押し込みは0.3秒の重複抑止、
  スロット切替は回転を0.8秒止めるまで次の切替を抑止します。
- `media_ptt`: 再生キーを押している間録音。短押しは取消、ダブルクリックは
  次スロットという既存PTTの動作を使います。曲送り／戻しでもスロットを切り替えます。
- `preset`を省略すると、`bindings`で指定したキーだけを使います。

`bindings`は既定の割り当てをキーごとに置き換えます。
`KEY_NEXTSONG: none`のように、既定の操作を無効にすることもできます。

## 指定できる操作

`ptt`、`record_toggle`、`cancel`、`volume_up`、`volume_down`、
`slot_next`、`slot_previous`、`none`を指定できます。
複雑なスクリプトやシェルコマンドの実行は許可しません。
音量の`amount`は1〜100、`debounce_seconds`と`quiet_seconds`は0〜60秒です。
`debounce_seconds`は最後に受け付けた操作からの重複を抑止し、
`quiet_seconds`は入力が止まるまで次を待ちます。PTTの押下・解放には指定できません。
録音中にその機器が切断された場合は、その機器で始めた録音だけを破棄します。

キーはLinuxの数値キーコード（例: `183`）で全キーを指定できます。
名前では音量・ミュート・再生・曲送り／戻し、`KEY_F1`〜`KEY_F24`、
`KEY_SPACE`、`KEY_ENTER`、`KEY_ESC`を指定できます。
通常の文字キーも数値指定なら利用できますが、パスワードなどの入力と
競合しないキーを選んでください。ホイールは既存のブラウザのスクロール処理を使い、
この設定のキー操作とは別に扱います。

## OSや他のアプリへキーを渡すか

`exclusive`の既定は`false`です。ARGOSがキーを読んでも、OSや他のアプリにも
元のキーが届きます。登録していないキーはARGOSでは操作しません。
`true`なら対象のeventノードを独占し、OSやWaydroidでの二重動作を防ぎます。
**そのノードの未登録キーも他のアプリへ届かなくなる**ため、専用ノブ・専用リモコン向けです。
普段使いのキーボードは原則`false`にしてください。同じキーを別の機器で押した場合には、
製品IDが違う限りARGOS操作は起きません。

新設定のためにlabwcへ全キーボード共通のキー割り当ては追加しません。
以前のインストーラーが追加した`XF86Audio*`の抑止が残っている場合は、
`docs/waydroid.md`の手順で必要な割り当てを解除してください。

## 既存設定との互換性

GPIOの`ptt.gpio`、Bluetoothの`ptt.bluetooth_mac`、旧USBノブの
`ptt.knob_device`はそのまま使えます。GPIOはLinuxキー入力ではないので別入力のままです。
旧USBノブ設定は内部で`media_knob`へ変換し、同じIDを`ptt.devices`に記入した場合は
そちらを優先します。旧Bluetooth設定と新しいID設定が同じ機器を指す場合も、
接続時に新設定側だけがキーを処理します。
その場合も、旧MAC設定で有効だったBluetooth接続・切断音は維持します。
設定画面の詳細項目では`ptt.devices`を配列として編集できます。
接続機器を選んでキーを学習する専用画面は、今回の変更には含めません。
