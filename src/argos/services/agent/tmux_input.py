"""端末へ貼り付ける本文の検証とUTF-8変換。"""

MAX_INPUT_BYTES = 256 * 1024


def encode_terminal_text(text: str) -> bytes:
    """起動や入力済み状態の記録より前に不正な本文を拒否する。"""
    encoded = text.encode("utf-8")
    if not encoded.strip() or len(encoded) > MAX_INPUT_BYTES or b"\0" in encoded:
        raise ValueError("端末入力は空白以外の文字を含む256KiB以下のNULなし文字列にしてください")
    return encoded
