"""ARGOS 利用前の本人確認を管理する。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta


KEYWORD_HASH_PREFIX = "pbkdf2_sha256"
KEYWORD_HASH_ITERATIONS = 210_000


@dataclass(frozen=True)
class AuthResult:
    """認証判定の結果。"""

    authenticated: bool
    message: str
    alert: bool = False


class AuthGate:
    """ロック状態と音声キーワード解除を管理する。"""

    def __init__(self, enabled: bool, keyword_hash: str, trust_seconds: int, failure_threshold: int) -> None:
        """認証設定と初期ロック状態を保持する。"""
        self._enabled = enabled
        self._keyword_hash = keyword_hash
        self._trust_duration = timedelta(seconds=trust_seconds)
        self._failure_threshold = failure_threshold
        self._trusted_until: datetime | None = None
        self._failures = 0

    @property
    def enabled(self) -> bool:
        """認証ゲートが有効か返す。"""
        return self._enabled

    def is_authenticated(self, now: datetime | None = None) -> bool:
        """現在の認証状態を返す。"""
        if not self._enabled:
            return True
        current = now or datetime.now().astimezone()
        return self._trusted_until is not None and current < self._trusted_until

    def mark_activity(self, now: datetime | None = None) -> None:
        """認証済みの有効期限を延長する。"""
        if not self._enabled:
            return
        current = now or datetime.now().astimezone()
        self._trusted_until = current + self._trust_duration

    def verify_keyword(self, phrase: str, now: datetime | None = None) -> AuthResult:
        """音声キーワードでロック解除を試みる。"""
        if not self._enabled:
            return AuthResult(True, "認証は無効です。")
        if not self._keyword_hash:
            self._failures += 1
            return AuthResult(False, "音声キーワードが未設定です。", self._failures >= self._failure_threshold)
        if verify_keyword_hashes(phrase.strip(), self._keyword_hash):
            self._failures = 0
            self.mark_activity(now)
            return AuthResult(True, "本人確認しました。")
        self._failures += 1
        return AuthResult(False, "音声キーワードが一致しません。", self._failures >= self._failure_threshold)

    def lock(self) -> None:
        """認証状態を破棄してロックする。"""
        self._trusted_until = None


def hash_keyword(keyword: str) -> str:
    """音声キーワードをPBKDF2ハッシュ形式へ変換する。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", keyword.encode("utf-8"), salt, KEYWORD_HASH_ITERATIONS)
    return "$".join(
        (
            KEYWORD_HASH_PREFIX,
            str(KEYWORD_HASH_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_keyword_hash(keyword: str, encoded_hash: str) -> bool:
    """音声キーワードが保存済みハッシュと一致するか判定する。"""
    try:
        prefix, iterations_text, salt_text, digest_text = encoded_hash.split("$", 3)
        if prefix != KEYWORD_HASH_PREFIX:
            return False
        iterations = int(iterations_text)
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected_digest = base64.urlsafe_b64decode(digest_text.encode("ascii"))
    except (ValueError, TypeError):
        return False
    actual_digest = hashlib.pbkdf2_hmac("sha256", keyword.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual_digest, expected_digest)


def verify_keyword_hashes(keyword: str, encoded_hashes: str) -> bool:
    """複数の保存済みハッシュのいずれかに音声キーワードが一致するか判定する。"""
    for encoded_hash in _split_keyword_hashes(encoded_hashes):
        if verify_keyword_hash(keyword, encoded_hash):
            return True
    return False


def _split_keyword_hashes(encoded_hashes: str) -> list[str]:
    """セミコロン、カンマ、改行区切りのキーワードハッシュを配列へ分割する。"""
    return [item.strip() for item in re.split(r"[;,\n]+", encoded_hashes) if item.strip()]
