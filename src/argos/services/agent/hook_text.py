"""CLI通知の任意テキストを、所有権情報と混同せず正規化する。"""


def optional_hook_text(value: object, name: str) -> str:
    """CLIの任意項目のnullだけを空文字にし、異常な型は黙って変換しない。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"フックの{name}は文字列にしてください")
    return value
