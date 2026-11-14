# tmuxスロットの子エージェント回答

## 目的と操作

親エージェントが一度回答して待機に戻った後でも、子の確定回答を回収する。
通常・SP・Grid共通で、履歴へ「子エージェントの回答です」と追加し、既存のスロット別音声キューへ渡す。
子の終了待ちで次の入力をロックしない。非選択スロットの音声は戻るまで保留し、録音中も保留する。
同時に届いた回答は、回答単位で順番に再生する。ミュートと明示取消は既存の操作に従う。
ブラウザを閉じている間もフックは保存するが、回収・読み上げは接続中の端末表示から行う。
通知の検知には端末readの間隔が加わる。スマートフォンへのプッシュ通知は含めない。

## CLIごとの取得方法

| CLI | 子の完了 | 親との対応付け |
|---|---|---|
| Claude Code | `SubagentStop` | 親session_id、agent_id、agent_type、last_assistant_message |
| Codex 0.162.1 | `SubagentStop` | 起動時に保存した親native IDと通知のsession_idを照合 |
| antigravity 1.4.0 | 子にも発火する`Stop` | 親`PostToolUse(invoke_subagent)`のstepIdxと生成結果に含まれる子ID・ログURIを照合 |

agyは専用の子完了フックがない。生成通知とログの反映順序が逆でも再照合する。
生成元が確認できない子、別の会話・世代、失敗・未確定本文は読み上げない。
親の`fullyIdle=false`でも、親自身の確定回答がある場合は親ターンを完了し、次の入力を受け付ける。

参照: [Claudeのフック](https://code.claude.com/docs/en/hooks#subagentstop)、
[Codexのフック](https://developers.openai.com/codex/hooks)、
[agyのフック](https://www.agy.dev/docs/hooks/)、
[agyの子エージェント](https://www.agy.dev/docs/subagents?tab=cli)。

## 保存とAPI

- 既存スロット状態配下の`background/`へ回答を保存する。ディレクトリ700、ファイル600。
- 世代・子ID・本文のハッシュで同じ通知を重複排除し、配信済み印を永続化する。
- 回答ごとの排他制御のみを取り、通常入力の`turn.lock`は取得しない。
- 本文は1件256KiB、保存はスロット当たり512件。配信済みも件数に含め、自動削除はしない。
- agyの子対応・完了・照合待ちは各64件。親ログ64MiB、1行1MiBを上限とする。
- 上限到達時はエラーとし、古い回答を黙って削除しない。
- 既存端末readに任意項目`background_ids`を追加する。旧版で省略された場合は空として扱う。
- 既存`POST /api/terminal/turn`の`input_mode=background`に、本文として回答IDを渡す。
  Bearer・Origin検査を共用し、新しい無認証経路を作らない。通常入力へのフォールバックはしない。
- リモートも同じ経路で取得し、接続先の選択スロットを変更しない。未対応版は503で拒否する。

## 制限と反映

「同じ子の同じ本文」は一度だけ配信するが、親が子の回答を引用した場合の意味的な重複除去は行わない。
通信の配信完了と、スピーカーから実際に再生されたことは別である。
配信後のブラウザ終了・ミュート・取消で失われた音声を自動再配信する保証はない。履歴は残る。
リモートでは接続先の本文回収後に手元でTTSを行うため、その間の障害では手元への音声再配信を保証しない。

Claude・Codexの新しいフックはCLI起動時に設定する。
起動済みCLIにはARGOS本体の再起動だけでは追加されない。会話を失うリセットを自動で行わず、
新規スロットまたは利用者が再起動を選んだCLIから適用する。恒久systemd連携は対象外。
agyを別workspaceで使う場合は、そのworkspaceにも同じ専用フックが必要。

## 検証

実データを使わない隔離したtmuxで3CLIを試験する。
親回答終了後の子回答取得、通常入力ロック保持中の取得、同一通知の配信済み除外を確認する。

```bash
ARGOS_TEST_SUBAGENTS=1 uv run pytest -q tests/test_subagent_live.py --no-cov
ARGOS_TEST_BACKGROUND_BROWSER=1 uv run --with playwright pytest -q tests/test_background_browser.py --no-cov
uv run pytest -q tests/test_background_answers.py tests/test_agy_subagents.py tests/test_background_delivery.py
node --test tests/js/*.js
```

実CLI試験は生成枠を使うため既定では実行しない。物理マイク・スピーカーの聞こえ方は別途受入確認する。
