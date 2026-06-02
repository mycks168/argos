from datetime import datetime, timedelta, timezone

from argos.services.auth import AuthGate, hash_keyword, verify_keyword_hash


def test_keyword_hash_verifies_original_keyword():
    """音声キーワードはハッシュ化して検証できる。"""
    encoded = hash_keyword("秘密の言葉")

    assert verify_keyword_hash("秘密の言葉", encoded) is True
    assert verify_keyword_hash("違う言葉", encoded) is False


def test_auth_gate_allows_when_disabled():
    """認証無効時は常に利用できる。"""
    gate = AuthGate(False, "", 1800, 3)

    assert gate.is_authenticated() is True
    assert gate.verify_keyword("なんでも").authenticated is True


def test_auth_gate_unlocks_with_keyword_and_expires():
    """キーワード成功後は一定時間だけ認証済みにする。"""
    now = datetime(2026, 6, 3, 8, tzinfo=timezone.utc)
    gate = AuthGate(True, hash_keyword("解除"), 60, 3)

    result = gate.verify_keyword("解除", now)

    assert result.authenticated is True
    assert gate.is_authenticated(now + timedelta(seconds=59)) is True
    assert gate.is_authenticated(now + timedelta(seconds=61)) is False


def test_auth_gate_alerts_after_repeated_failures():
    """失敗回数がしきい値に達したら警戒フラグを返す。"""
    gate = AuthGate(True, hash_keyword("解除"), 1800, 2)

    first = gate.verify_keyword("違う")
    second = gate.verify_keyword("まだ違う")

    assert first.alert is False
    assert second.alert is True
