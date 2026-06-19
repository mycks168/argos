import tempfile
import time

from argos.services.tts.cache import TTSCacheManager


def test_tts_cache_basic():
    """短いテキストのTTS結果を話者ID別に保存して取得できる。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = TTSCacheManager(tmpdir, max_chars=10, max_size_mb=1)

        assert manager.get("hello", 1) is None

        data = b"dummy_wav"
        manager.set("hello", 1, data)

        assert manager.get("hello", 1) == data
        assert manager.get("world", 1) is None
        assert manager.get("hello", 2) is None

        manager.set("longtext_over_limit", 1, b"too_long")
        assert manager.get("longtext_over_limit", 1) is None


def test_tts_cache_lru():
    """容量上限を超えた場合は、最終アクセスが古いWAVから削除する。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = TTSCacheManager(tmpdir, max_chars=10, max_size_mb=0.000014)

        manager.set("aaa", 1, b"12345")
        time.sleep(0.01)
        manager.set("bbb", 1, b"12345")

        time.sleep(0.01)
        assert manager.get("aaa", 1) == b"12345"

        time.sleep(0.01)
        manager.set("ccc", 1, b"12345")

        assert manager.get("aaa", 1) == b"12345"
        assert manager.get("ccc", 1) == b"12345"
        assert manager.get("bbb", 1) is None
