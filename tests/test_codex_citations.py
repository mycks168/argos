"""Codexの検索出典復元とCLI連携を確認する。"""

import json
from pathlib import Path

import pytest

from test_codex_cli import _settings

from argos.services.codex.citations import (
    CitationSource,
    format_citation_sources,
    load_citation_sources,
)
from argos.services.codex.cli import CodexCliClient


def _write_session(codex_home: Path, session_id: str) -> None:
    """検索結果を含む最小のCodexセッションを作成する。"""
    session_file = codex_home / "sessions" / "2026" / "08" / f"rollout-{session_id}.jsonl"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "type": "event_msg",
        "payload": {
            "type": "web_search_end",
            "results": [
                {
                    "ref_id": "turn23search9",
                    "title": "  公式  サイト ",
                    "url": "https://example.com/official",
                },
                {
                    "ref_id": "turn23search0",
                    "title": "参考記事",
                    "url": "https://example.net/article",
                },
                {
                    "ref_id": "turn23search8",
                    "title": "危険なURL",
                    "url": "javascript:alert(1)",
                },
            ],
        },
    }
    session_file.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")


def test_load_citation_sources_preserves_requested_order(tmp_path: Path) -> None:
    """引用順でHTTP出典だけをセッションから復元する。"""
    session_id = "019fea23-a755-7721-ada4-fb18c7798446"
    codex_home = tmp_path / "codex-home"
    _write_session(codex_home, session_id)

    sources = load_citation_sources(
        codex_home,
        session_id,
        ("turn23search0", "turn23search9", "turn23search8"),
    )

    assert sources == (
        CitationSource("turn23search0", "参考記事", "https://example.net/article"),
        CitationSource("turn23search9", "公式 サイト", "https://example.com/official"),
    )
    assert format_citation_sources(sources).startswith("\n\n出典:\n- 参考記事")


def test_codex_client_replaces_internal_citation_with_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """CLI回答では内部タグを除き、検索元URLを回答末尾へ追加する。"""
    session_id = "019fea23-a755-7721-ada4-fb18c7798446"
    settings = _settings(tmp_path)

    class FakeStdin:
        """プロンプト入力を受け付ける最小スタブ。"""

        def write(self, _text: str) -> None:
            """入力本文はこのテストでは使用しない。"""

        def close(self) -> None:
            """閉じる操作を受け付ける。"""

    class FakeStderr:
        """空の標準エラーを返すスタブ。"""

        def read(self) -> str:
            """エラーなしとして空文字を返す。"""
            return ""

    class FakeProcess:
        """引用付きCodex JSONLを返すプロセススタブ。"""

        def __init__(self, command: list[str]) -> None:
            """出力先を含むコマンドを保持する。"""
            self.command = command
            self.stdin = FakeStdin()
            self.stderr = FakeStderr()
            self.stdout = iter(
                [
                    json.dumps({"type": "thread.started", "thread_id": session_id}) + "\n",
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "type": "agent_message",
                                "text": "回答です。\ue200cite\ue202turn23search9\ue201",
                            },
                        }
                    )
                    + "\n",
                ]
            )

        def wait(self, timeout: float | None = None) -> int:
            """最終出力と検索セッションを作成して正常終了する。"""
            assert timeout == 10
            output_file = Path(self.command[self.command.index("-o") + 1])
            output_file.write_text("回答です。\ue200cite\ue202turn23search9\ue201", encoding="utf-8")
            _write_session(Path(settings.codex_home), session_id)
            return 0

    def fake_popen(command: list[str], **_kwargs: object) -> FakeProcess:
        """固定のCodexプロセススタブを返す。"""
        return FakeProcess(command)

    monkeypatch.setattr("argos.services.codex.cli.subprocess.Popen", fake_popen)

    response = CodexCliClient(settings).ask("調べて")

    assert "\ue200" not in response
    assert response == "回答です。\n\n出典:\n- 公式 サイト\n  https://example.com/official"
