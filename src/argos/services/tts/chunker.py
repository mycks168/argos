"""読み上げテキストの分割処理。"""

from __future__ import annotations

import re


DEFAULT_DELIMITERS = "。！？!?"


class TextChunker:
    """差分テキストを句読点や改行で読み上げ単位へ分割する。"""

    def __init__(self, delimiters: str = DEFAULT_DELIMITERS) -> None:
        """空のバッファで初期化する。"""
        self._buffer = ""
        if delimiters:
            escaped = re.escape(delimiters)
            pattern = rf"[{escaped}][\s　]?|\n+"
        else:
            pattern = r"\n+"
        self._break_pattern = re.compile(pattern)

    def push(self, text: str) -> list[str]:
        """テキスト差分を追加し、確定した読み上げチャンクを返す。"""
        self._buffer += text
        chunks: list[str] = []
        while True:
            match = self._break_pattern.search(self._buffer)
            if not match:
                break
            cut = match.end()
            chunk = self._buffer[:cut].strip()
            self._buffer = self._buffer[cut:]
            if chunk:
                chunks.append(chunk)
        return chunks

    def flush(self) -> str:
        """残っている未確定テキストを返してバッファを空にする。"""
        chunk = self._buffer.strip()
        self._buffer = ""
        return chunk
