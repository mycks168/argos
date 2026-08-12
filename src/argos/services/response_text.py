"""エージェント応答を画面表示用と読み上げ用へ整形する。"""

from __future__ import annotations

import re

CITATION_START = "\ue200"
CITATION_DELIMITER = "\ue202"
CITATION_STOP = "\ue201"
SOURCE_SECTION_PREFIX = "\n\n出典:\n"
SOURCE_ID_PATTERN = re.compile(r"^turn\d+[A-Za-z_-]+\d+$")
HTTP_URL_PATTERN = re.compile(r"https?://\S+")
MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(https?://[^)]+\)")


class CitationStreamFilter:
    """分割された応答から内部引用タグを除き、参照IDを保持する。"""

    def __init__(self) -> None:
        """未完了タグと参照IDの保存領域を初期化する。"""
        self._pending = ""
        self._citation_ids: list[str] = []

    @property
    def citation_ids(self) -> tuple[str, ...]:
        """出現順を保った重複なしの参照IDを返す。"""
        return tuple(self._citation_ids)

    def push(self, text: str) -> str:
        """応答差分を受け取り、完全な引用タグだけを除去して返す。"""
        remaining = self._pending + text
        self._pending = ""
        visible_parts: list[str] = []
        while remaining:
            marker_start = remaining.find(CITATION_START)
            if marker_start < 0:
                visible_parts.append(remaining)
                break
            visible_parts.append(remaining[:marker_start])
            marker_stop = remaining.find(CITATION_STOP, marker_start + 1)
            if marker_stop < 0:
                self._pending = remaining[marker_start:]
                break
            self._remember_marker(remaining[marker_start + 1 : marker_stop])
            remaining = remaining[marker_stop + 1 :]
        return "".join(visible_parts)

    def finish(self) -> str:
        """閉じていない不正なタグは情報欠落を避けるため本文へ戻す。"""
        pending = self._pending
        self._pending = ""
        return pending

    def _remember_marker(self, marker_body: str) -> None:
        """cite形式のマーカーから有効な参照IDだけを記録する。"""
        parts = [part.strip() for part in marker_body.split(CITATION_DELIMITER)]
        if not parts or parts[0] != "cite":
            return
        for source_id in parts[1:]:
            if SOURCE_ID_PATTERN.fullmatch(source_id) and source_id not in self._citation_ids:
                self._citation_ids.append(source_id)


def strip_citations(text: str) -> tuple[str, tuple[str, ...]]:
    """完成済み応答から内部引用タグを除き、参照IDを返す。"""
    citation_filter = CitationStreamFilter()
    visible_text = citation_filter.push(text) + citation_filter.finish()
    return visible_text, citation_filter.citation_ids


def text_for_speech(text: str) -> str:
    """画面向け応答から出典一覧とURLを除き、読み上げ本文だけを返す。"""
    visible_text, _ = strip_citations(text)
    source_index = visible_text.find(SOURCE_SECTION_PREFIX)
    if source_index >= 0:
        visible_text = visible_text[:source_index]
    visible_text = MARKDOWN_LINK_PATTERN.sub(r"\1", visible_text)
    return HTTP_URL_PATTERN.sub("", visible_text)
