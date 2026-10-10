"""ARGOS のエントリーポイント。"""

from __future__ import annotations

import logging
import sys

from argos.config import ensure_runtime_dirs, load_settings
from argos.core.app import ArgosApp

# ダッシュボードから再起動を求められて止まったときの終了コード。
# 0以外で終わるので、systemdのRestart=on-failureが起動し直す。
RESTART_EXIT_CODE = 75


def main() -> None:
    """設定を読み込み、ARGOS を起動する。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ensure_runtime_dirs()
    app = ArgosApp(load_settings())
    app.run()
    if app.restart_requested:
        sys.exit(RESTART_EXIT_CODE)


if __name__ == "__main__":
    main()

