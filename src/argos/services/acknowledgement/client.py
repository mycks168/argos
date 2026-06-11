import logging
import random
import requests

log = logging.getLogger(__name__)


class AcknowledgementClient:
    """ユーザーの発話内容に応じて最適な待機フレーズを決定するAPIのクライアント。"""

    def __init__(self, base_url: str, bearer_token: str) -> None:
        """APIのベースURLとBearerトークンを初期化する。

        引数:
            base_url: APIサーバーのベースURL。
            bearer_token: 認証用のBearerトークン。
        """
        self._base_url = base_url.rstrip("/")
        self._bearer_token = bearer_token

    def select_phrase(self, user_text: str, default_phrases: tuple[str, ...]) -> str:
        """ユーザーの発話テキストに応じて、最適な待機フレーズを取得する。

        API呼び出しに失敗した場合や、API設定がない場合は、default_phrasesからランダムに選択します。

        引数:
            user_text: ユーザーの発話テキスト。
            default_phrases: APIが利用できない場合の候補フレーズ。

        戻り値:
            選択された返答フレーズ。
        """
        if not self._base_url or not self._bearer_token:
            return random.choice(default_phrases)

        try:
            response = requests.post(
                f"{self._base_url}/select",
                json={"text": user_text, "phrases": list(default_phrases)},
                headers={"Authorization": f"Bearer {self._bearer_token}"},
                timeout=5,
            )
            if response.status_code == 200:
                data = response.json()
                if "phrase" in data:
                    return str(data["phrase"])
            else:
                log.warning("Acknowledgement API returned status code: %d", response.status_code)
        except Exception as exc:
            log.warning("Failed to call Acknowledgement API: %s", exc)

        # フォールバック
        return random.choice(default_phrases)
