import os
import random
import yaml
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

app = FastAPI(title="ARGOS Acknowledgement API")

# Bearer認証の定義
security = HTTPBearer()

# 環境変数から設定値を取得。
API_TOKEN = os.environ.get("ACKNOWLEDGEMENT_API_TOKEN", "argos-token")
RULES_PATH = os.environ.get("ACKNOWLEDGEMENT_RULES_PATH")
if not RULES_PATH:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    RULES_PATH = os.path.join(base_dir, "rules.yml")


class RequestBody(BaseModel):
    text: str
    phrases: list[str] = []


class ResponseBody(BaseModel):
    phrase: str


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """Bearerトークンの検証を行う。

    引数:
        credentials: リクエストヘッダーから抽出された認証情報。

    戻り値:
        検証済みのトークン文字列。

    例外:
        HTTPException: トークンが無効な場合。
    """
    if credentials.credentials != API_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


def load_rules() -> list[dict]:
    """定義ファイルから語尾判定ルールをロードする。

    定義ファイルが存在しないか壊れている場合は、空のリストを返す。

    戻り値:
        ルールのリスト。各ルールは {'keywords': [...], 'phrase': '...'} 形式。
    """
    if not os.path.exists(RULES_PATH):
        return []
    try:
        with open(RULES_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if isinstance(data, dict) and "rules" in data:
                return list(data["rules"])
    except Exception:
        pass
    return []


@app.post("/select", response_model=ResponseBody)
def select_phrase(body: RequestBody, token: str = Depends(verify_token)) -> ResponseBody:
    """ユーザーの発話テキストに応じて、最適な待機フレーズを選択する。

    引数:
        body: ユーザー発話と候補フレーズを含むリクエストボディ。
        token: 検証済みのBearerトークン（Depends経由）。

    戻り値:
        選択または生成された返答フレーズを含むレスポンス。
    """
    text = body.text.strip()
    rules = load_rules()

    # 定義ファイルのルールに沿ってマッチング
    for rule in rules:
        keywords = rule.get("keywords", [])
        phrase = rule.get("phrase", "")
        if keywords and phrase and any(text.endswith(k) for k in keywords):
            return ResponseBody(phrase=phrase)

    # フォールバック：候補フレーズからランダムに選択。候補が空ならデフォルトフレーズ
    if body.phrases:
        phrase = random.choice(body.phrases)
    else:
        phrase = "わかった。少し待ってね。"

    return ResponseBody(phrase=phrase)
