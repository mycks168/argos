"""一定間隔で処理を実行するバックグラウンド監視スレッド。

利用枠取得やWi-Fi状態取得のように「起動してから終了まで、一定間隔で
何かを取得してダッシュボードへ反映する」定型のポーリングを共通化する。
各監視は起動直後に一度実行し、以降は間隔を空けて繰り返す。停止は共有の
shutdown イベントで行う（デーモンスレッドのため明示的なjoinは不要）。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable


log = logging.getLogger(__name__)


class PeriodicMonitor:
    """shutdown まで一定間隔でコールバックを実行するデーモンスレッド。"""

    def __init__(
        self,
        name: str,
        interval_seconds: float,
        refresh: Callable[[], None],
        shutdown: threading.Event,
    ) -> None:
        """監視名、実行間隔、実行するコールバック、停止イベントを保持する。"""
        self._name = name
        self._interval_seconds = interval_seconds
        self._refresh = refresh
        self._shutdown = shutdown
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """監視スレッドをバックグラウンドで起動する。"""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        """起動直後に一度実行し、以降は間隔を空けて繰り返す。"""
        while not self._shutdown.is_set():
            self._refresh()
            if self._shutdown.wait(self._interval_seconds):
                return
