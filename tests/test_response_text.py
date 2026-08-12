"""応答本文の引用除去と読み上げ整形を確認する。"""

from argos.services.dashboard.state import DashboardState
from argos.services.response_text import (
    CitationStreamFilter,
    strip_citations,
    text_for_speech,
)


def test_strip_citations_removes_multiple_source_marker() -> None:
    """複数出典を含む内部タグを本文から除き、参照順を保持する。"""
    text = "回答です。\ue200cite\ue202turn23search9\ue202turn23search0\ue201"

    visible, citation_ids = strip_citations(text)

    assert visible == "回答です。"
    assert citation_ids == ("turn23search9", "turn23search0")


def test_citation_stream_filter_handles_split_marker() -> None:
    """ネットワーク差分の途中で分割された引用タグも露出させない。"""
    citation_filter = CitationStreamFilter()

    assert citation_filter.push("回答。\ue200cite\ue202turn1se") == "回答。"
    assert citation_filter.push("arch2\ue201続き") == "続き"
    assert citation_filter.finish() == ""
    assert citation_filter.citation_ids == ("turn1search2",)


def test_citation_stream_filter_preserves_unclosed_marker() -> None:
    """壊れた未終端タグは回答本文を欠落させないよう最後に戻す。"""
    citation_filter = CitationStreamFilter()

    assert citation_filter.push("回答。\ue200cite\ue202broken") == "回答。"
    assert citation_filter.finish() == "\ue200cite\ue202broken"


def test_text_for_speech_omits_sources_and_urls() -> None:
    """画面用の出典一覧とURLは読み上げ本文へ渡さない。"""
    text = "回答です。\n\n出典:\n- 公式サイト\n  https://example.com/source"

    assert text_for_speech(text) == "回答です。"
    assert text_for_speech("詳細は https://example.com です") == "詳細は  です"


def test_restored_history_hides_legacy_citation_marker() -> None:
    """保存済み履歴に残る旧引用タグも再起動後の画面へ露出させない。"""
    state = DashboardState()
    state.restore_histories(
        {
            "codex\0調査": [
                {
                    "role": "assistant",
                    "text": "回答。\ue200cite\ue202turn23search0\ue201",
                }
            ]
        }
    )

    assert state.slot_messages("調査", "codex")[0]["text"] == "回答。"
