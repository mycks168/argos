"""USBメディアノブのLinux入力イベントをARGOS操作へ変換する。

ノブの押し込みは、押した瞬間ではなく離したときに押下と解放がまとめて届く。
そのため押下ごとに録音の開始と停止を切り替える。
"""

from __future__ import annotations

import logging
import os
import select
import struct
import threading
import time
from collections.abc import Callable
from pathlib import Path


log = logging.getLogger(__name__)
_EVENT = struct.Struct("@llHHi")
_EV_KEY = 1
KEY_MUTE = 113
KEY_VOLUMEDOWN = 114
KEY_VOLUMEUP = 115
KEY_NEXTSONG = 163
KEY_PREVIOUSSONG = 165
# 回転をこの秒数だけ止めるまで、次のスロット切り替えを受け付けない。
SLOT_QUIET_SECONDS = 0.8
# 押し込みの連続入力（チャタリング）とみなす間隔。
TOGGLE_DEBOUNCE_SECONDS = 0.3
VOLUME_STEP = 5


def parse_device_id(value: str) -> tuple[str, str] | None:
    """`514c:8850` 形式のUSB機器IDを、小文字のベンダーIDと製品IDへ分ける。"""
    vendor, sep, product = value.strip().lower().partition(":")
    if not sep or not vendor or not product:
        return None
    return vendor.rjust(4, "0"), product.rjust(4, "0")


def has_key(capabilities: str, code: int) -> bool:
    """sysfsのキー対応ビット列に、指定キーが含まれるか返す。"""
    bits = struct.calcsize("l") * 8
    words = capabilities.split()
    index = len(words) - 1 - code // bits
    if index < 0:
        return False
    try:
        return bool(int(words[index], 16) >> (code % bits) & 1)
    except ValueError:
        return False


def find_knob_device(device_id: str, input_root: Path = Path("/sys/class/input")) -> Path | None:
    """USB機器IDに一致し、ミュートキーを送れるイベントデバイスを探す。

    同じ機器がキーボードとマウスの2つに分かれるため、キー側だけを選ぶ。
    """
    parsed = parse_device_id(device_id)
    if parsed is None:
        return None
    for event in sorted(input_root.glob("event*")):
        device = event / "device"
        try:
            vendor = (device / "id/vendor").read_text().strip().lower()
            product = (device / "id/product").read_text().strip().lower()
            capabilities = (device / "capabilities/key").read_text()
        except OSError:
            continue
        if (vendor, product) == parsed and has_key(capabilities, KEY_MUTE):
            return Path("/dev/input") / event.name
    return None


class UsbKnobInput:
    """ノブの押し込み・音量の回転・曲送り戻しの回転を監視し、抜き差しにも追従する。"""

    def __init__(
        self,
        device_id: str,
        on_toggle: Callable[[], None],
        on_volume: Callable[[int], None],
        on_next: Callable[[], None],
        on_previous: Callable[[], None],
        on_disconnect: Callable[[], None],
        shutdown: threading.Event,
        clock: Callable[[], float] = time.monotonic,
        start: bool = True,
    ) -> None:
        """監視する機器と各操作のコールバックを受け取る。"""
        self._device_id = device_id
        self._on_toggle = on_toggle
        self._on_volume = on_volume
        self._on_next = on_next
        self._on_previous = on_previous
        self._on_disconnect = on_disconnect
        self._shutdown = shutdown
        self._clock = clock
        self._last_toggle_at: float | None = None
        self._last_slot_event_at: float | None = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="usb-knob-input")
        if start:
            self._thread.start()

    def _handle_key(self, code: int, value: int) -> None:
        """キー入力を操作へ変換する。解放と自動反復は使わない。"""
        if value != 1:
            return
        now = self._clock()
        if code == KEY_MUTE:
            if self._last_toggle_at is not None and now - self._last_toggle_at < TOGGLE_DEBOUNCE_SECONDS:
                return
            self._last_toggle_at = now
            self._on_toggle()
        elif code == KEY_VOLUMEUP:
            self._on_volume(VOLUME_STEP)
        elif code == KEY_VOLUMEDOWN:
            self._on_volume(-VOLUME_STEP)
        elif code in (KEY_NEXTSONG, KEY_PREVIOUSSONG):
            # 勢いよく回しても1つだけ進むよう、回転が止まるまで次を受け付けない。
            last = self._last_slot_event_at
            self._last_slot_event_at = now
            if last is not None and now - last < SLOT_QUIET_SECONDS:
                return
            (self._on_next if code == KEY_NEXTSONG else self._on_previous)()

    def _read_device(self, path: Path) -> None:
        """機器が抜かれるか終了するまで入力イベントを読む。"""
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
            self._on_disconnect()

    def _run(self) -> None:
        """抜き差しのたびにイベントデバイスを探し直す。"""
        while not self._shutdown.is_set():
            path = find_knob_device(self._device_id)
            if path is not None:
                try:
                    log.info("USBメディアノブの監視を開始します: %s", path)
                    self._read_device(path)
                except Exception:
                    log.exception("USBメディアノブの監視に失敗しました")
            self._shutdown.wait(1)
