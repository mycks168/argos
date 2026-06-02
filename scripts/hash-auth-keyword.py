#!/usr/bin/env python3
"""音声キーワードをARGOS_AUTH_KEYWORD_HASH用のハッシュへ変換する。"""

from __future__ import annotations

import getpass

from argos.services.auth import hash_keyword


def main() -> None:
    """標準入力から音声キーワードを受け取り、ハッシュを表示する。"""
    keyword = getpass.getpass("音声キーワード: ")
    print(hash_keyword(keyword))


if __name__ == "__main__":
    main()
