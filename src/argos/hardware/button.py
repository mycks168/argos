"""PTT ボタンの状態管理。"""

from __future__ import annotations

import time
from enum import Enum
from threading import Lock
from typing import Callable


DOUBLE_CLICK_MAX_PRESS_SEC = 0.35
DOUBLE_CLICK_MAX_GAP_SEC = 0.50


class PttState(Enum):
    """PTT の現在状態。"""

    IDLE = "idle"
    LISTENING = "listening"
    BUSY = "busy"


class ButtonPtt:
    """PTT ボタン押下とダブルクリックをアプリイベントへ変換する。"""

    def __init__(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_double_click: Callable[[], None],
        on_cancel: Callable[[], None],
    ) -> None:
        """コールバックを受け取り、初期状態を作成する。"""
        self._on_press = on_press
        self._on_release = on_release
        self._on_double_click = on_double_click
        self._on_cancel = on_cancel
        self._state = PttState.IDLE
        self._lock = Lock()
        self._press_started_at = 0.0
        self._last_quick_release_at = 0.0

    @property
    def state(self) -> PttState:
        """現在の PTT 状態を返す。"""
        return self._state

    def mark_busy(self) -> None:
        """Codex/TTS 処理中として状態を更新する。"""
        with self._lock:
            self._state = PttState.BUSY

    def mark_idle(self) -> None:
        """処理中なら待機状態へ戻す。"""
        with self._lock:
            if self._state == PttState.BUSY:
                self._state = PttState.IDLE

    def handle_press(self) -> None:
        """物理ボタン押下イベントを処理する。"""
        callback = None
        with self._lock:
            self._press_started_at = time.monotonic()
            if self._state == PttState.BUSY:
                self._state = PttState.LISTENING
                callback = self._on_press
            elif self._state != PttState.IDLE:
                return
            else:
                self._state = PttState.LISTENING
                callback = self._on_press
        if callback:
            callback()

    def handle_release(self) -> None:
        """物理ボタン解放イベントを処理する。"""
        callbacks = []
        with self._lock:
            if self._state != PttState.LISTENING:
                return
            now = time.monotonic()
            duration = now - self._press_started_at
            if duration < DOUBLE_CLICK_MAX_PRESS_SEC:
                gap = now - self._last_quick_release_at
                self._state = PttState.IDLE
                callbacks.append(self._on_cancel)
                if gap < DOUBLE_CLICK_MAX_GAP_SEC:
                    self._last_quick_release_at = 0.0
                    callbacks.append(self._on_double_click)
                else:
                    self._last_quick_release_at = now
            else:
                self._state = PttState.BUSY
                callbacks.append(self._on_release)
        for callback in callbacks:
            callback()
