"""エージェント応答を画面表示用と読み上げ用へ整形する。"""

from __future__ import annotations

import re

CITATION_START = "\ue200"
CITATION_DELIMITER = "\ue202"
CITATION_STOP = "\ue201"
SOURCE_SECTION_PREFIX = "\n\n出典:\n"
SOURCE_ID_PATTERN = re.compile(r"^turn\d+[A-Za-z_-]+\d+$")
HTTP_URL_PATTERN = re.compile(r"https?://\S+")
MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\([^)]*\)")
MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\([^)]*\)")
SOURCE_SECTION_TITLE = "出典:"
# 行頭の字下げと引用記号。
_LINE_PREFIX_PATTERN = re.compile(r"^[ \t]*(?:>[ \t]*)*")
# 見出しと箇条書きの記号。
_BLOCK_MARKER_PATTERN = re.compile(r"^(?:#{1,6}[ \t]+|[-*+][ \t]+(?:\[[ xX]\][ \t]+)?|\d+[.)][ \t]+)")
_FENCE_OPEN_PATTERN = re.compile(r"^(`{3,}|~{3,})")
_SVG_OPEN_PATTERN = re.compile(r"^<svg[\s>]", re.IGNORECASE)
_RULE_PATTERN = re.compile(r"^(?:[-*_=][ \t]*){3,}$")
# 行の先頭がこの形の間は、続きが届くまで種類を決められない。
_UNDECIDED_PATTERN = re.compile(r"^(?:`|~|\||<svg|[-*_=#\s]*$)", re.IGNORECASE)
_MIN_DECIDABLE_LENGTH = 4
# 行の途中でも、ここまで届けば先に読み上げへ回してよい区切り。
_SPEECH_BREAK_PATTERN = re.compile(r"[。、！？\s]")
_INLINE_MARK_PATTERN = re.compile(r"[*`]+|~~")
_BLANK_RUN_PATTERN = re.compile(r"\n{3,}")


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


class SpeechStreamFilter:
    """分割されて届く画面向け応答から、読み上げる本文だけを取り出す。

    コードブロック（mermaidの図やSVGを含む）、本文に直接書かれたSVG、表、区切り線、
    出典一覧、画像、URLは読み上げない。見出しや箇条書きの記号、強調の記号は外して本文だけを残す。
    最初の一言を早く話し始められるよう、行の途中でも句読点や空白まで届いた分は先に返す。
    """

    def __init__(self) -> None:
        """行の途中経過と、読み飛ばし中の状態を初期化する。"""
        self._citations = CitationStreamFilter()
        self._buffer = ""
        self._mid_line = False
        self._fence = ""
        self._in_svg = False
        self._in_sources = False
        self._previous_blank = True

    def push(self, text: str) -> str:
        """応答差分を受け取り、読み上げてよいと確定した分を返す。"""
        self._buffer += self._citations.push(text)
        spoken: list[str] = []
        while True:
            newline = self._buffer.find("\n")
            if newline < 0:
                break
            line = self._buffer[:newline]
            self._buffer = self._buffer[newline + 1 :]
            spoken.append(self._take_line(line))
            spoken.append("\n")
        spoken.append(self._take_partial())
        return "".join(spoken)

    def finish(self) -> str:
        """応答の終わりに、残っている行を読み上げ本文として返す。"""
        self._buffer += self._citations.finish()
        line = self._buffer
        self._buffer = ""
        return self._take_line(line)

    def _take_line(self, line: str) -> str:
        """1行ぶん（行の残りを含む）を受け取り、読み上げる文字列を返す。"""
        if self._mid_line:
            # 行の前半は先に返してあるので、残りは行内の記号だけ外す。
            self._mid_line = False
            self._previous_blank = False
            return _inline_for_speech(line)
        previous_blank = self._previous_blank
        body = _LINE_PREFIX_PATTERN.sub("", line).rstrip()
        self._previous_blank = not body
        if self._in_sources:
            return ""
        if self._fence:
            if _is_fence_close(body, self._fence):
                self._fence = ""
            return ""
        if self._in_svg:
            self._in_svg = "</svg>" not in body.lower()
            return ""
        fence = _FENCE_OPEN_PATTERN.match(body)
        if fence:
            self._fence = fence.group(1)
            return ""
        if _SVG_OPEN_PATTERN.match(body):
            self._in_svg = "</svg>" not in body.lower()
            return ""
        if body == SOURCE_SECTION_TITLE and previous_blank:
            self._in_sources = True
            return ""
        if body.startswith("|") or _RULE_PATTERN.match(body):
            return ""
        return _inline_for_speech(_BLOCK_MARKER_PATTERN.sub("", body))

    def _take_partial(self) -> str:
        """改行がまだ届いていない行のうち、先に読み上げてよい前半だけを返す。"""
        if self._in_sources or self._fence or self._in_svg:
            return ""
        if not self._mid_line:
            body = _LINE_PREFIX_PATTERN.sub("", self._buffer)
            # 行頭だけでは、図・表・見出しなどの始まりかどうかを決められない間は待つ。
            if len(body) < _MIN_DECIDABLE_LENGTH or _UNDECIDED_PATTERN.match(body):
                return ""
            if SOURCE_SECTION_TITLE.startswith(body) or body.startswith(SOURCE_SECTION_TITLE):
                return ""
            # 箇条書きの記号の直後は、チェックボックスが続くかどうかが決まるまで待つ。
            item = _BLOCK_MARKER_PATTERN.sub("", body)
            if item != body and len(item) < _MIN_DECIDABLE_LENGTH:
                return ""
        cut = _safe_cut(self._buffer)
        if cut <= 0:
            return ""
        head = self._buffer[:cut]
        self._buffer = self._buffer[cut:]
        if self._mid_line:
            return _inline_for_speech(head)
        self._mid_line = True
        self._previous_blank = False
        body = _LINE_PREFIX_PATTERN.sub("", head)
        return _inline_for_speech(_BLOCK_MARKER_PATTERN.sub("", body))


def _is_fence_close(body: str, fence: str) -> bool:
    """コードブロックを閉じる行か判定する（開始と同じ記号が、同じ数以上並ぶ行）。"""
    stripped = body.strip()
    return len(stripped) >= len(fence) and set(stripped) == {fence[0]}


def _safe_cut(text: str) -> int:
    """書きかけのリンクやURLを途中で切らずに、先に読み上げへ回せる長さを返す。"""
    cut = 0
    for match in _SPEECH_BREAK_PATTERN.finditer(text):
        cut = match.end()
    open_bracket = text.rfind("[", 0, cut)
    if open_bracket >= 0 and _bracket_end(text, open_bracket) >= cut:
        # 書きかけ、または切れ目をまたぐリンク・画像は、その手前までにとどめる。
        cut = open_bracket
        if cut > 0 and text[cut - 1] == "!":
            cut -= 1
    return cut


def _bracket_end(text: str, open_bracket: int) -> int:
    """「[」で始まるリンクや画像の終わりの位置を返す。まだ終わりが届いていなければ文字列の長さを返す。"""
    close = text.find("]", open_bracket)
    if close < 0 or close + 1 >= len(text):
        return len(text)
    if text[close + 1] != "(":
        return close
    end = text.find(")", close)
    return end if end >= 0 else len(text)


def _inline_for_speech(text: str) -> str:
    """行内の画像、リンク、URL、強調やコードの記号を外し、読み上げる文字だけを残す。"""
    text = MARKDOWN_IMAGE_PATTERN.sub("", text)
    text = MARKDOWN_LINK_PATTERN.sub(r"\1", text)
    text = HTTP_URL_PATTERN.sub("", text)
    return _INLINE_MARK_PATTERN.sub("", text)


def text_for_speech(text: str) -> str:
    """完成済みの画面向け応答から、読み上げ本文だけを返す。"""
    speech_filter = SpeechStreamFilter()
    spoken = speech_filter.push(text) + speech_filter.finish()
    # 読み飛ばした行のぶんだけ残る空行を詰める。
    return _BLANK_RUN_PATTERN.sub("\n\n", spoken).rstrip("\n")
