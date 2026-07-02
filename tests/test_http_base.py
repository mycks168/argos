"""共通HTTPハンドラ(JsonRequestHandler)とBearer照合のテスト。"""

from __future__ import annotations

import io

import pytest

from argos.services.http_base import JsonRequestHandler, bearer_header_matches


class _FakeHandler(JsonRequestHandler):
    """ソケットを介さず _read_json を検証するためのハンドラ。"""

    def __init__(self, headers: dict[str, str], body: bytes = b"") -> None:
        """ヘッダーとボディを直接差し込む。"""
        self.headers = headers
        self.rfile = io.BytesIO(body)


def _handler(body: bytes, length: int | None = None) -> _FakeHandler:
    if length is None:
        length = len(body)
    return _FakeHandler({"Content-Length": str(length)}, body)


def test_bearer_header_matches():
    """Bearerトークンが一致するときだけTrueを返す。"""
    assert bearer_header_matches("Bearer abc", "abc") is True
    assert bearer_header_matches("Bearer abcd", "abc") is False
    assert bearer_header_matches("", "abc") is False
    assert bearer_header_matches("abc", "abc") is False


def test_read_json_parses_object():
    """正しいJSONオブジェクトを辞書として読める。"""
    assert _handler(b'{"a": 1}')._read_json(1024) == {"a": 1}


def test_read_json_rejects_oversized():
    """上限を超えるボディはValueErrorにする。"""
    with pytest.raises(ValueError):
        _handler(b'{"a": 1}', length=2000)._read_json(1024)


def test_read_json_rejects_non_object():
    """JSONオブジェクト以外はValueErrorにする。"""
    with pytest.raises(ValueError):
        _handler(b"[1, 2]")._read_json(1024)


def test_read_json_rejects_broken_json():
    """壊れたJSONはValueErrorにする。"""
    with pytest.raises(ValueError):
        _handler(b"{not json")._read_json(1024)


def test_read_json_empty_allowed():
    """allow_empty=True なら空ボディを空辞書として扱う。"""
    handler = _FakeHandler({"Content-Length": "0"}, b"")
    assert handler._read_json(1024, allow_empty=True) == {}


def test_read_json_empty_rejected_by_default():
    """既定では空ボディをValueErrorにする。"""
    handler = _FakeHandler({"Content-Length": "0"}, b"")
    with pytest.raises(ValueError):
        handler._read_json(1024)
