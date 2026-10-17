# Codex・agyの常駐ターミナル試験

Claudeに続き、Codexとagy（provider名は`antigravity`）を同じtmuxスロットへ接続する。
従来のCLI方式は残し、既存スロットを置き換えない。通常/SP/Gridの既存画面とAPIを利用する。

## この環境で試す

設定末尾に「Codex常駐テスト」と「agy常駐テスト」を追加した。既存6件の順番・既定スロットは変えない。
PTT巡回から除外するため、画面で明示的に選択する。
再起動後に画面を再読み込みすると、対象スロットの会話領域が実ターミナルになる。
声が出せない場合は、端末内か従来のテキスト入力欄から入力できる。共通ミュートも従来どおり利用する。

初回だけ、起動したCLIで「準備完了」と返す短い接続確認を実行する。
これはユーザー本文の二重送信ではなく、CLIの初期化とフックの接続確認。初回の生成枠を1回使用する。
接続確認が終わるまで最初の文字入力を待たせ、描画初期化中の欠落や起動確認回答との混同を防ぐ。
確認が失敗した場合、ユーザー本文を追加送信しない。ログインやworkspaceの信頼確認は事前に済ませる。

端末内のSpaceは文字入力。「入力取消」は書きかけを消す操作で、実行中のエージェントの停止とは別。
Enterはtmuxの送信キーへ正規化し、Codexの拡張キー交渉の違いで本文だけ入力欄に残る問題を防ぐ。

## 設定例

既存の`agent.slots`へ追加する。

```yaml
- type: local
  name: Codex常駐テスト
  provider: codex
  execution_mode: tmux
  cwd: /opt/argos
  ptt_cycle: false
  tmux_timeout_seconds: 1800
- type: local
  name: agy常駐テスト
  provider: antigravity
  execution_mode: tmux
  cwd: /opt/argos
  ptt_cycle: false
  tmux_timeout_seconds: 1800
```

実行コマンドとモデルは既存の設定解決を使う。`execution_mode`未指定は従来の`cli`。
Codexの`codex_home`を指定している場合は、このtmuxセッションだけの`CODEX_HOME`へ引き継ぐ。
常駐Codexの起動には`--config check_for_update_on_startup=false`を追加し、更新案内による入力待ちを防ぐ。
個人のconfig.toml・従来CLI方式・他providerには適用しない。更新は運用側で明示的に行う。
起動済みの常駐Codexには再起動・同じ会話IDでの再開が必要。Enter専用ボタンは追加しない。
既存台帳の設定指紋を変えないよう、ユーザー指定コマンドではなく起動時に引数を付加する。
権限とsandboxも既存設定に従う。tmux方式でprint/exec/resume等を指定する追加引数は拒否する。
試作では`-c`による追加設定も拒否する。共通設定や別セッションへの接続でフックや所有権を崩さないため。

## フックと回答確定

- Codex: 起動時の`--config`でSessionStart/Stop/Interruptを指定する。ユーザーのconfig.tomlは変更しない。
- Codex: 自作の記録フックを使うため、当該起動は`--dangerously-bypass-hook-trust`を指定する。このフラグはその起動の他の有効フックにも作用するので、同時に読み込むフックをレビューした環境でのみ利用する。
- agy: リポジトリの`.agents/hooks.json`に`argos-tmux`を追加する。専用環境変数がない起動では何もしないため、従来スロットや手動起動を記録しない。グローバル設定は変更しない。
- 別workspaceでagyを使う場合は、同じ名前付きフックをそのworkspaceの`.agents/hooks.json`へ既存定義を残して導入する。タブを開いただけで他リポジトリへ設定ファイルを自動作成しない。
- 各起動のARGOS世代とCLIのnative会話IDを台帳で固定する。古い世代は無視し、別会話IDへの切替はエラーにする。
- CLI内の`/new`等で会話IDを変える操作は未対応。新しい会話はARGOSのセッションリセットから開始する。
- Codexの完了重複はturn_id、agyは確定MODEL/PLANNER_RESPONSEのstep_indexで除外する。agyのexecutionNumは識別子として使わない。
- agyのStopには本文がないので、通知に対応するtranscript_full.jsonlの新しいDONE回答だけを取得する。短いログ反映待ちを設け、過去回答・ツール結果・未確定行は返さない。
- agyログの読込上限は64MiB、1行は1MiB。反映待ちは0.1秒間隔で20回。正規化したフック台帳の上限は既存同様16MiB。
- 中断、非idle、空回答、ログ取得失敗を成功にしない。古い回答を代わりに読み上げない。

質問は起動時コンテキストで通常文を依頼し、ユーザーは自由文で返せる。
全てのツールや将来のCLI版の選択UIを必ず抑制できる保証ではない。承認や対話画面を機械的に自動選択する処理は追加しない。

## 試験

実CLIの試験は生成枠を使用する。通常のpytestではスキップする。

```sh
ARGOS_TEST_TMUX_AGENTS=1 uv run pytest tests/test_tmux_agents_live.py --override-ini addopts='' -v
ARGOS_TEST_CONSOLE_BROWSER=1 uv run --with playwright pytest tests/test_console_browser.py --override-ini addopts='' -v
```

実CLI試験は、合言葉保持・クライアント再生成・質問への選択肢外の自由回答を両方で確認する。
実ブラウザ試験は通常/SP/Gridの直接入力・模擬録音・合成PTTイベント・回答WAV・リサイズ・再接続を含む。
物理マイクと聴取試験ではない。STT/TTSはスタブ、CLIとtmuxとChromiumは実物。端末画面から本文を抽出する試験ではない。
試験専用セッションだけを作成・終了し、実運用スロットの会話は使わない。

恒久的なsystemd連携・OS再起動後のCLI復元・未確定ターンの自動再送は引き続き対象外。
画面からの再接続と、終了したCLIの再起動・resumeは区別する。

## 仕様確認元

更新案内の抑止は起動・provider分離の21件で確認し、起動制御モジュールの行カバレッジは100%。
全体pytestは1,166件成功・10件スキップ、カバレッジ88.01%。
品質ゲートcheckでは対象2ファイルのRuff・strict mypy成功、重複0、新規未使用コードなし。
vultureが検出した既存試験のMock.return_value属性3件はモック設定として利用されるため削除しない。
依存の追加やサービス層から上位へのimport追加はない。
実運用のCodexは同じ会話ID・paneで再開し、更新確認無効の引数と更新案内の非表示を確認した。
Runner・本体の再読込は返事終了後の`argos-codex-no-update-20261018.timer`で実施する。

- [Codex公式フック仕様](https://learn.chatgpt.com/docs/hooks)
- [Codex公式設定仕様](https://developers.openai.com/codex/config-reference/)
- [agy公式フック仕様](https://www.agy.dev/docs/hooks/)
- 搭載CLIの`--help`と、隔離セッションでの実測を併用。公式にない環境変数でagyのホームを差し替える方式は採らない。
