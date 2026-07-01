# tts-filter

VOICEVOX などの TTS に渡す前に、技術文書・LLM応答・Markdown を読み上げ向けに正規化する Python フィルタです。

## できること

- `README.md` のようなファイル名を読みやすく正規化
- `LLM`, `API`, `URL`, `VOICEVOX`, `OpenClaw`, `Gitea`, `gitignore`, `image`, `JPEG`, `PNG`, `autossh`, `GPSD`, `Reminder`, `tmp`, `tmpfs`, `tmux`, `ttyd`, `service`, `uv`, `pytest`, `OFF`, `MCP` などの技術単語や状態表記を辞書で管理
- `5タップ`, `誤変換`, `鳥羽` などの日本語単語や地名も辞書で管理
- path, version, date, time, URL, email を読み上げ向けに整形
- テストや検証文脈の `通ってる` や `通る` など、文脈依存の読みは正規表現ルールで管理
- YAML 辞書を API 経由で登録・変更・削除
- Bearer 認証つき HTTP API を提供

## 辞書

辞書は `src/tts_filter/dictionary.yml` にあります。

- `acronyms`: 略語や技術単語の読み
- `terms`: 日本語など、単語境界を使いにくい語の読み
- `extensions`: 拡張子の読み
- `phrase_rules`: 文脈依存の読みを補正する正規表現ルール

## セットアップ

```bash
uv sync
```

## API起動

`.env.example` をコピーして `.env` を作ります。

```bash
cp .env.example .env
```

起動:

```bash
uv run uvicorn tts_filter_api.app:app --host 0.0.0.0 --port 8000
```

## API

- `GET /health`
- `POST /normalize`
- `GET /dictionary`
- `PUT /dictionary`
- `PATCH /dictionary`
- `DELETE /dictionary`

`/normalize` では以下も渡せます。

- `code_block_mode`: `skip` / `meta` / `first-line` / `rule` / `ollama-summary` / `literal`
- `ollama_model`: 使うローカル Ollama モデル名

### 例

```bash
curl -H "Authorization: Bearer $TTS_FILTER_BEARER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"text":"README.md と LLM"}' \
  http://localhost:8000/normalize
```

```bash
curl -X PUT -H "Authorization: Bearer $TTS_FILTER_BEARER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"category":"acronyms","key":"GITEA","value":"ギテア"}' \
  http://localhost:8000/dictionary
```

## CLI

引数で渡す:

```bash
uv run tts-filter 'README.md と LLM'
```

標準入力から渡す:

```bash
echo 'README.md と LLM' | uv run tts-filter
```

別辞書を使う:

```bash
uv run tts-filter --config src/tts_filter/dictionary.yml 'README.md と LLM'
```

## コードブロック処理

- インラインコード: そのまま読み上げ変換
- コードブロック: デフォルトで `ollama-summary`

### モード一覧

| モード | 説明 | 例 |
|---|---|---|
| `skip` | 言語名だけ読む | `bash のコードブロックがあります` |
| `meta` | 言語名＋行数 | `bash、3行のコードブロックです` |
| `first-line` | 最初の行（コメント記号除去）を読む | `bash：uv run pytest` |
| `rule` | コメント行優先、なければコマンド名＋行数 | `bash、3行。依存パッケージをインストール` |
| `ollama-summary` | Ollama で一文に要約（要 Ollama） | `依存パッケージをインストールします` |
| `literal` | コード本文をそのまま読み上げ変換 | — |

Ollama が入っていない、またはモデルが無い場合は、要約できなかった旨を返します。

CLI 例:

```bash
uv run tts-filter --code-block-mode rule < input.md
uv run tts-filter --code-block-mode ollama-summary --ollama-model qwen2.5:0.5b < input.md
```

## systemd デーモン化

`tts-filter.service` をユーザー systemd に登録して常駐させます。サービスは `/opt/tts-filter` にインストールされ、ポート `9191` で起動します。
辞書YAMLはリクエストごとに読み直すため、単語や正規表現ルールの追加はサービス再起動なしで反映されます。

```bash
# /opt/tts-filter にファイルをインストール
sudo cp -r . /opt/tts-filter

# .env を配置（トークンなどを設定しておく）
sudo cp .env /opt/tts-filter/.env

# サービスファイルをユーザー systemd ディレクトリにコピー
cp tts-filter.service ~/.config/systemd/user/

# 有効化して起動
systemctl --user daemon-reload
systemctl --user enable --now tts-filter

# 状態確認
systemctl --user status tts-filter

# ログ確認
journalctl --user -u tts-filter -f
```

起動後は `http://localhost:9191` でアクセスできます。

```bash
curl -H "Authorization: Bearer $TTS_FILTER_BEARER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"text":"README.md と LLM"}' \
  http://localhost:9191/normalize
```

ホスト・ポートを変えたい場合は `tts-filter.service` の `ExecStart` を編集後、`systemctl --user daemon-reload && systemctl --user restart tts-filter` で反映します。

## テスト

```bash
uv run pytest
```
