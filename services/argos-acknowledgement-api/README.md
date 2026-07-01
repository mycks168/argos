# ARGOS Acknowledgement API

ARGOS（Autonomous Road Guardian & Observation System）がユーザーの発話に対して、最適な初期待機応答（進捗フレーズ）を決定するための外部APIサービスです。

## 機能

- **語尾判定によるフレーズ決定**:
  - 「〜見て」「〜見せて」などの語尾 -> `今見てみるね。`
  - 「〜知ってる？」「〜知っていますか？」などの語尾 -> `確認するね。`
  - 「〜調べて」「〜教えて」などの語尾 -> `すぐ調べるね。`
  - 「〜やって」「〜動かして」などの語尾 -> `了解。やってみるね。`
  - 「〜どこだっけ」「〜どこですか」などの語尾 -> `調べてみるね。`
  - 「〜ないよね」「〜ないよね？」などの語尾 -> `調べてみるね。`
  - その他 -> ARGOSから送信された候補フレーズからランダム選択
- **Bearer認証**:
  - APIへのすべてのリクエストは `Authorization: Bearer <TOKEN>` ヘッダーによる認証が必要です。

## セットアップと起動

### 依存関係のインストール

本プロジェクトは `uv` を使用して管理されています。

```bash
uv sync
```

### 起動方法

環境変数 `ACKNOWLEDGEMENT_API_TOKEN` に認証トークンを設定して起動します（デフォルト値は `argos-token` です）。

```bash
export ACKNOWLEDGEMENT_API_TOKEN="your-secret-token"
uv run uvicorn main:app --host 127.0.0.1 --port 23032
```

## API 仕様

### `POST /select`

ユーザーの発話テキストに最適な返答フレーズを選択して返します。

- **ヘッダー**:
  - `Authorization: Bearer <TOKEN>`
  - `Content-Type: application/json`

- **リクエストボディ**:
  ```json
  {
    "text": "画面を見せて",
    "phrases": [
      "わかった。少し待ってね。",
      "了解。やってみるね。",
      "確認するね。"
    ]
  }
  ```

- **レスポンスボディ**:
  ```json
  {
    "phrase": "今見てみるね。"
  }
  ```

## テストの実行

```bash
uv run pytest --cov=. --cov-report=term-missing
```
