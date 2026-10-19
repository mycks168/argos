"""親と子の後続回答を、経路に依存しない同じ表示本文へそろえる。"""

PARENT_REPLY_PREFIX = "後続の回答です。\n"
CHILD_REPLY_PREFIX = "子エージェントの回答です。\n"


def background_text(text: str, kind: str = "child") -> str:
    """リモート中継・履歴回収・音声取得で案内を二重に付けない。"""
    if text.startswith((PARENT_REPLY_PREFIX, CHILD_REPLY_PREFIX)):
        return text
    return (PARENT_REPLY_PREFIX if kind == "parent" else CHILD_REPLY_PREFIX) + text
