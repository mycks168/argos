---
name: argos-tools
description: ARGOSのツール置き場にあるツール（地図の拡大・縮小、ルート全体表示、GPS追従、画面配置、ダッシュボードの表示を消す、ナビ開始など）を、一覧を見て実行する。利用者の依頼がツールで片付きそうなときは、個別のスキルを読む前に、まずこれを使う。
---

# ARGOSのツール置き場

ARGOSのツールは、各スキルのフォルダの `router.yaml` と `~/.config/argos/router-tools/*.yaml` に定義されている。
`argos-tool` コマンドで、一覧を見て実行できる。ツールは増えていくので、毎回一覧を確かめる。

```bash
cd /opt/argos
# 使えるツールと引数の一覧（引数名の後ろの ? は省略可）
uv run argos-tool list
# 実行する（引数はJSON）。結果はJSONで返る（ok, reply, stdout, stderr）
uv run argos-tool run map_zoom --args '{"direction": "in"}'
uv run argos-tool run map_navigate --args '{"destination": "熊本駅"}'
```

- 一覧に「（実行前に利用者へ確認）」とあるツールは、実行してよいか利用者に確かめてから実行する。
- `ok` が `false` なら、`stderr` を見て原因を伝えるか、個別のスキル（`SKILL.md`）の手順で対処する。
- 成功したら、`reply` の文をもとに短く伝える。
- 引数の決め方が分からない、ツールで片付かない依頼は、今までどおり個別のスキルや自分の判断で対応する。
- ツールの定義を足す・直すときは、`docs/basic_design.md` の「ツール置き場」の書き方に従う。保存すれば、ARGOSの再起動なしで使える。
