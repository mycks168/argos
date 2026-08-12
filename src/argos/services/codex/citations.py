"""CodexセッションからWeb検索の出典情報を復元する。"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from argos.services.response_text import SOURCE_SECTION_PREFIX

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CitationSource:
    """画面へ表示する一つのWeb出典を表す。"""

    source_id: str
    title: str
    url: str


def load_citation_sources(
    codex_home: Path,
    session_id: str,
    citation_ids: tuple[str, ...],
) -> tuple[CitationSource, ...]:
    """現在セッションの検索イベントから指定された出典を出現順で返す。"""
    if not session_id or not citation_ids:
        return ()
    session_file = _find_session_file(codex_home, session_id)
    if session_file is None:
        return ()
    sources_by_id = _read_sources(session_file, set(citation_ids))
    return tuple(sources_by_id[source_id] for source_id in citation_ids if source_id in sources_by_id)


def format_citation_sources(sources: tuple[CitationSource, ...]) -> str:
    """出典を既存のプレーンテキスト履歴でも扱えるURL一覧へ変換する。"""
    if not sources:
        return ""
    lines = [SOURCE_SECTION_PREFIX.rstrip("\n")]
    for source in sources:
        lines.append(f"- {source.title}\n  {source.url}")
    return "\n".join(lines)


def _find_session_file(codex_home: Path, session_id: str) -> Path | None:
    """セッションIDをファイル名に持つ最新のJSONLを探す。"""
    sessions_dir = codex_home.expanduser() / "sessions"
    if not sessions_dir.exists():
        return None
    try:
        candidates = list(sessions_dir.rglob(f"*{session_id}.jsonl"))
        return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None
    except OSError:
        log.exception("Codex引用元のセッションファイル検索に失敗しました: %s", sessions_dir)
        return None


def _read_sources(session_file: Path, wanted_ids: set[str]) -> dict[str, CitationSource]:
    """Web検索完了イベントから必要な参照IDだけを読み取る。"""
    sources: dict[str, CitationSource] = {}
    for line in _session_lines(session_file):
        if not any(source_id in line for source_id in wanted_ids):
            continue
        _collect_event_sources(line, wanted_ids, sources)
    return sources


def _session_lines(session_file: Path) -> Iterator[str]:
    """セッションファイルを失敗時に空として一行ずつ返す。"""
    try:
        with session_file.open(encoding="utf-8") as session_lines:
            yield from session_lines
    except OSError:
        log.exception("Codex引用元のセッションファイル読取に失敗しました: %s", session_file)


def _web_search_results(line: str) -> list[object]:
    """一つのJSONL行がWeb検索完了イベントなら検索結果を返す。"""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return []
    if not isinstance(event, dict):
        return []
    payload = event.get("payload", {})
    if not isinstance(payload, dict) or payload.get("type") != "web_search_end":
        return []
    results = payload.get("results", [])
    return results if isinstance(results, list) else []


def _citation_source(result: object, wanted_ids: set[str]) -> CitationSource | None:
    """検索結果が要求された安全な出典なら型付きデータへ変換する。"""
    if not isinstance(result, dict):
        return None
    source_id = str(result.get("ref_id", ""))
    url = str(result.get("url", ""))
    if source_id not in wanted_ids or not _is_http_url(url):
        return None
    title = " ".join(str(result.get("title", "")).split()) or urlparse(url).netloc
    return CitationSource(source_id, title, url)


def _collect_event_sources(
    line: str,
    wanted_ids: set[str],
    sources: dict[str, CitationSource],
) -> None:
    """一つのJSONLイベントに含まれる構造化検索結果を出典へ追加する。"""
    for result in _web_search_results(line):
        source = _citation_source(result, wanted_ids)
        if source is None:
            continue
        sources[source.source_id] = source


def _is_http_url(url: str) -> bool:
    """ブラウザへ渡してよいHTTPまたはHTTPS URLか判定する。"""
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
