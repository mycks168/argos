from argos.services.tts.chunker import TextChunker


def test_text_chunker_splits_by_punctuation_and_flushes_rest():
    chunker = TextChunker()

    assert chunker.push("こんにちは。次") == ["こんにちは。"]
    assert chunker.push("です。\n残り") == ["次です。"]
    assert chunker.flush() == "残り"


def test_text_chunker_does_not_split_by_dot_by_default():
    chunker = TextChunker()

    assert chunker.push("systemd.service を確認します. 続き") == []
    assert chunker.push("です。") == ["systemd.service を確認します. 続きです。"]


def test_text_chunker_uses_custom_delimiters():
    chunker = TextChunker("。.,")

    assert chunker.push("README.md, systemd") == [
        "README.",
        "md,",
    ]
    assert chunker.flush() == "systemd"


def test_text_chunker_allows_newline_only():
    chunker = TextChunker("")

    assert chunker.push("一文目。二文目\n三文目") == ["一文目。二文目"]
    assert chunker.flush() == "三文目"
