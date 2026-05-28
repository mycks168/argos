"""Raspberry Pi GPIO の PTT 入力。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable


log = logging.getLogger(__name__)


class GpioPttInput:
    """gpiozero の Button を PTT 入力として扱う。"""

    def __init__(self, pin: int, on_press: Callable[[], None], on_release: Callable[[], None]) -> None:
        """BCM ピン番号とコールバックで GPIO を初期化する。"""
        try:
            from gpiozero import Button  # pyright: ignore[reportMissingImports]
            from gpiozero.exc import BadPinFactory  # pyright: ignore[reportMissingImports]
        except ModuleNotFoundError as exc:
            raise RuntimeError("gpiozero が見つかりません。`uv sync` を実行して依存関係を更新してください。") from exc

        self._ready = False
        try:
            self._button = Button(pin, pull_up=True, bounce_time=0.05)
        except BadPinFactory as exc:
            raise RuntimeError(
                "GPIO pin factory を初期化できません。`uv sync` で lgpio を導入し、"
                "必要なら `GPIOZERO_PIN_FACTORY=lgpio uv run argos` で起動してください。"
            ) from exc
        self._button.when_pressed = self._handle_press
        self._button.when_released = self._handle_release
        self._on_press = on_press
        self._on_release = on_release
        threading.Timer(1.5, self._enable).start()
        log.info("GPIO%d を PTT 入力として初期化しました", pin)

    def _enable(self) -> None:
        """起動直後の不安定なイベントを無視した後で入力を有効化する。"""
        self._ready = True

    def _handle_press(self) -> None:
        """gpiozero の押下イベントをアプリへ渡す。"""
        if self._ready:
            self._on_press()

    def _handle_release(self) -> None:
        """gpiozero の解放イベントをアプリへ渡す。"""
        if self._ready:
            self._on_release()
