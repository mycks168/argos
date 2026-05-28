"""ARGOS のエントリーポイント。"""

from __future__ import annotations

import logging

from argos.config import ensure_runtime_dirs, load_settings
from argos.core.app import ArgosApp


def main() -> None:
    """設定を読み込み、ARGOS を起動する。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ensure_runtime_dirs()
    app = ArgosApp(load_settings())
    app.run()


if __name__ == "__main__":
    main()

