from unittest.mock import MagicMock
from argos.services.acknowledgement import AcknowledgementClient


def test_select_phrase_no_url():
    """URLやトークンが設定されていない場合、デフォルトフレーズから選択されることをテストする。"""
    client = AcknowledgementClient("", "")
    default_phrases = ("わかった。少し待ってね。", "了解。やってみるね。")
    phrase = client.select_phrase("テスト", default_phrases)
    assert phrase in default_phrases


def test_select_phrase_success(monkeypatch):
    """APIリクエストが成功した場合、APIから返されたフレーズが選択されることをテストする。"""
    client = AcknowledgementClient("http://api", "token")
    default_phrases = ("デフォルトフレーズ",)

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"phrase": "今見てみるね。"}

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: FakeResponse())

    phrase = client.select_phrase("画面を見て", default_phrases)
    assert phrase == "今見てみるね。"


def test_select_phrase_api_error(monkeypatch):
    """APIがエラーコードを返した場合、デフォルトフレーズにフォールバックすることをテストする。"""
    client = AcknowledgementClient("http://api", "token")
    default_phrases = ("フォールバックフレーズ",)

    class FakeResponse:
        status_code = 500

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: FakeResponse())

    phrase = client.select_phrase("画面を見て", default_phrases)
    assert phrase == "フォールバックフレーズ"


def test_select_phrase_exception(monkeypatch):
    """API呼び出し中に例外が発生した場合、デフォルトフレーズにフォールバックすることをテストする。"""
    client = AcknowledgementClient("http://api", "token")
    default_phrases = ("フォールバックフレーズ",)

    def fake_post(*args, **kwargs):
        raise RuntimeError("API timeout")

    monkeypatch.setattr("requests.post", fake_post)

    phrase = client.select_phrase("画面を見て", default_phrases)
    assert phrase == "フォールバックフレーズ"
