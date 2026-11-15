"""応答本文の引用除去と読み上げ整形を確認する。"""

from argos.services.dashboard.state import DashboardState
from argos.services.response_text import (
    CitationStreamFilter,
    SpeechStreamFilter,
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


RICH_RESPONSE = """## 構成はこう
まず**全体像**を説明するね。`app.py`が入口だよ。[公式](https://example.com/docs)もどうぞ。

```mermaid
graph TD
A-->B
```

| 項目 | 値 |
|---|---|
| a | 1 |

---
> 引用だよ。
- 一つ目、ここ
- [x] 済んだもの
1. 番号つき

<svg width="1">
<circle/>
</svg>
![図](/uploads/a.png)
~~~
波線のコード
~~~
最後の一言。"""

RICH_SPEECH = [
    "構成はこう",
    "まず全体像を説明するね。app.pyが入口だよ。公式もどうぞ。",
    "引用だよ。",
    "一つ目、ここ",
    "済んだもの",
    "番号つき",
    "最後の一言。",
]


def _spoken_lines(text: str) -> list[str]:
    """読み上げ本文を、空行を除いた行の一覧にする。"""
    return [line for line in text.split("\n") if line.strip()]


def test_text_for_speech_skips_figures_tables_and_code() -> None:
    """図・表・コード・画像・区切り線は読まず、見出しや箇条書きは記号を外して読む。"""
    assert _spoken_lines(text_for_speech(RICH_RESPONSE)) == RICH_SPEECH


def test_speech_stream_filter_is_independent_of_delta_boundaries() -> None:
    """応答がどこで分割されて届いても、読み上げる本文は変わらない。"""
    for size in (1, 2, 3, 5, 8, 13, 40):
        speech_filter = SpeechStreamFilter()
        spoken = "".join(
            speech_filter.push(RICH_RESPONSE[index : index + size]) for index in range(0, len(RICH_RESPONSE), size)
        )
        spoken += speech_filter.finish()

        assert _spoken_lines(spoken) == RICH_SPEECH, f"{size}文字ごとの分割で結果が変わった"


def test_speech_stream_filter_releases_sentences_before_line_ends() -> None:
    """最初の一言を早く話せるよう、行が終わる前でも句読点まで届いた分は先に返す。"""
    speech_filter = SpeechStreamFilter()

    assert speech_filter.push("こんにちは、今日は") == "こんにちは、"
    assert speech_filter.push("いい天気。で") == "今日はいい天気。"
    assert speech_filter.finish() == "で"


def test_speech_stream_filter_holds_undecided_line_start() -> None:
    """行頭だけでは図や表の始まりか決められない間は、読み上げへ回さない。"""
    speech_filter = SpeechStreamFilter()

    assert speech_filter.push("``") == ""
    assert speech_filter.push("`mermaid\ngraph TD、A\n") == "\n\n"
    assert speech_filter.push("```\n| 表 、です |\n") == "\n\n"
    assert speech_filter.push("出典") == ""
    assert speech_filter.push("は、こちら。") == "出典は、こちら。"


def test_speech_stream_filter_keeps_split_links_and_urls_silent() -> None:
    """リンクやURLが分割されて届いても、URLの一部を読み上げない。"""
    speech_filter = SpeechStreamFilter()
    spoken = speech_filter.push("詳しくは、[公式 サイト](https://exam")
    spoken += speech_filter.push("ple.com/a b)と https://example.")
    spoken += speech_filter.push("org/x を、![絵](/up")
    spoken += speech_filter.push("loads/a.png)見てね。")
    spoken += speech_filter.finish()

    assert spoken == "詳しくは、公式 サイトと  を、見てね。"


def test_speech_stream_filter_hides_citations_and_sources() -> None:
    """内部の引用タグと、空行に続く出典一覧は読み上げない。"""
    speech_filter = SpeechStreamFilter()
    spoken = speech_filter.push("回答。\ue200cite\ue202turn1se")
    spoken += speech_filter.push("arch2\ue201続き。\n\n出典:\n- 公式\n")
    spoken += speech_filter.push("まだ出典")
    spoken += speech_filter.finish()

    assert spoken.strip() == "回答。続き。"
    # 文中の「出典:」は一覧の始まりではないので読む。
    assert text_for_speech("本文\n出典: 公式サイト") == "本文\n出典: 公式サイト"
