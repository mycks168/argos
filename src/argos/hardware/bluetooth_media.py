"""BluetoothメディアボタンのLinux入力イベントをARGOS操作へ変換する。"""

from __future__ import annotations

import logging
import os
import select
import struct
import threading
from collections.abc import Callable
from pathlib import Path


log = logging.getLogger(__name__)
_EVENT = struct.Struct("@llHHi")
_EV_KEY = 1
_PLAY_PAUSE = 164
_NEXT = 163
_PREVIOUS = 165


def find_media_device(mac: str, input_root: Path = Path("/sys/class/input")) -> Path | None:
    """Bluetoothアドレスに一致するイベントデバイスを探す。"""
    for event in sorted(input_root.glob("event*")):
        try:
            uniq = (event / "device/uniq").read_text().strip()
        except OSError:
            continue
        if uniq.casefold() == mac.casefold():
            return Path("/dev/input") / event.name
    return None


class BluetoothMediaInput:
    """再生の押下・解放と曲送りを監視し、再接続にも追従する。"""

    def __init__(
        self,
        mac: str,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_next: Callable[[], None],
        on_previous: Callable[[], None],
        on_cancel: Callable[[], None],
        shutdown: threading.Event,
    ) -> None:
        """監視する機器と各操作のコールバックを受け取る。"""
        self._mac = mac
        self._on_press = on_press
        self._on_release = on_release
        self._on_next = on_next
        self._on_previous = on_previous
        self._on_cancel = on_cancel
        self._shutdown = shutdown
        self._pressed = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="bluetooth-media-input")
        self._thread.start()

    def _handle_key(self, code: int, value: int) -> None:
        """キー押下と解放を処理し、長押しの自動反復は無視する。"""
        if code == _PLAY_PAUSE:
            if value == 1 and not self._pressed:
                self._pressed = True
                self._on_press()
            elif value == 0 and self._pressed:
                self._pressed = False
                self._on_release()
        elif value == 1 and code == _NEXT:
            self._on_next()
        elif value == 1 and code == _PREVIOUS:
            self._on_previous()

    def _read_device(self, path: Path) -> None:
        """機器が切断されるか終了するまで入力イベントを読む。"""
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            pending = b""
            while not self._shutdown.is_set():
                readable, _, _ = select.select([fd], [], [], 1)
                if not readable:
                    continue
                chunk = os.read(fd, _EVENT.size * 32)
                if not chunk:
                    break
                pending += chunk
                while len(pending) >= _EVENT.size:
                    _, _, kind, code, value = _EVENT.unpack_from(pending)
                    pending = pending[_EVENT.size :]
                    if kind == _EV_KEY:
                        self._handle_key(code, value)
        finally:
            os.close(fd)
            if self._pressed:
                self._pressed = False
                self._on_cancel()

    def _run(self) -> None:
        """Bluetooth再接続時にイベントデバイスを探し直す。"""
        while not self._shutdown.is_set():
            path = find_media_device(self._mac)
            if path is not None:
                try:
                    log.info("Bluetoothメディアボタンの監視を開始します: %s", path)
                    self._read_device(path)
                except Exception:
                    log.exception("Bluetoothメディアボタンの監視に失敗しました")
            self._shutdown.wait(0.1)
