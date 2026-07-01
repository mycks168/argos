"""TTS合成結果のローカルキャッシュ。"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


class TTSCacheManager:
    """短いTTS音声合成のWAVデータをローカルにキャッシュする。"""

    def __init__(self, cache_dir: str, max_chars: int, max_size_mb: int) -> None:
        self.cache_dir = Path(os.path.expanduser(cache_dir)).resolve()
        self.max_chars = max_chars
        self.max_size_bytes = max_size_mb * 1024 * 1024

    def get(self, text: str, speaker_id: int | str) -> bytes | None:
        """キャッシュからWAVデータを取得する。ヒット時はmtimeを更新する。"""
        if len(text) > self.max_chars:
            return None

        file_path = self._get_cache_path(text, speaker_id)
        if not file_path.exists():
            return None

        try:
            file_path.touch(exist_ok=True)
            return file_path.read_bytes()
        except Exception:
            log.exception("TTSキャッシュの読み込みに失敗しました: %s", file_path)
            return None

    def set(self, text: str, speaker_id: int | str, wav_data: bytes) -> None:
        """音声合成結果をキャッシュへ保存し、容量超過時は古いファイルを削除する。"""
        if len(text) > self.max_chars:
            return

        file_path = self._get_cache_path(text, speaker_id)
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(wav_data)
            file_path.touch(exist_ok=True)
            self._cleanup()
        except Exception:
            log.exception("TTSキャッシュの書き込みに失敗しました: %s", file_path)

    def _get_cache_path(self, text: str, speaker_id: int | str) -> Path:
        """テキストと話者IDからキャッシュファイルパスを生成する。"""
        key = f"{text}_{speaker_id}".encode("utf-8")
        file_name = f"{hashlib.md5(key).hexdigest()}.wav"
        return self.cache_dir / file_name

    def _cleanup(self) -> None:
        """キャッシュ総量が上限を超えたら、mtimeが古い順に削除する。"""
        if not self.cache_dir.exists():
            return

        files: list[tuple[Path, int, float]] = []
        total_size = 0
        for entry in os.scandir(self.cache_dir):
            if entry.is_file() and entry.name.endswith(".wav"):
                stat = entry.stat()
                files.append((Path(entry.path), stat.st_size, stat.st_mtime))
                total_size += stat.st_size

        if total_size <= self.max_size_bytes:
            return

        files.sort(key=lambda item: item[2])
        for path, size, _ in files:
            if total_size <= self.max_size_bytes:
                break
            try:
                path.unlink(missing_ok=True)
                total_size -= size
            except Exception:
                log.exception("TTSキャッシュの削除に失敗しました: %s", path)
