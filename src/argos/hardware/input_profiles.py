"""USB・BluetoothのLinux入力機器を、製品ID別の操作設定で扱う。"""

from __future__ import annotations

import fcntl
import json
import logging
import math
import os
import re
import select
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)
EVENT = struct.Struct("@llHHi")
EVIOCGRAB = 0x40044590
KEY_CODES = {
    "KEY_MUTE": 113, "KEY_VOLUMEDOWN": 114, "KEY_VOLUMEUP": 115,
    "KEY_PLAYPAUSE": 164, "KEY_NEXTSONG": 163, "KEY_PREVIOUSSONG": 165,
    "KEY_SPACE": 57, "KEY_ENTER": 28, "KEY_ESC": 1,
    **{f"KEY_F{i}": 58 + i for i in range(1, 11)},
    "KEY_F11": 87, "KEY_F12": 88,
    **{f"KEY_F{i}": 170 + i for i in range(13, 25)},
}
ACTIONS = {"ptt", "record_toggle", "cancel", "volume_up", "volume_down", "slot_next", "slot_previous", "none"}
PRESETS = {
    "media_ptt": {"KEY_PLAYPAUSE": "ptt", "KEY_NEXTSONG": "slot_next", "KEY_PREVIOUSSONG": "slot_previous"},
    "media_knob": {
        "KEY_MUTE": {"action": "record_toggle", "debounce_seconds": 0.3},
        "KEY_VOLUMEUP": "volume_up", "KEY_VOLUMEDOWN": "volume_down",
        "KEY_NEXTSONG": {"action": "slot_next", "quiet_seconds": 0.8},
        "KEY_PREVIOUSSONG": {"action": "slot_previous", "quiet_seconds": 0.8},
    },
}


def product_id(value: str) -> str:
    """ベンダーIDと製品IDを正規化し、不正なIDは設定エラーにする。"""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{1,4}:[0-9a-fA-F]{1,4}", value.strip()):
        raise ValueError("入力機器のidはベンダーID:製品ID（例: 514c:8850）で指定してください")
    return ":".join(f"{int(part, 16):04x}" for part in value.strip().split(":"))


def key_code(value: object) -> int:
    """よく使うLinuxキー名、または数値キーコードを受け取る。"""
    if isinstance(value, str) and value in KEY_CODES:
        return KEY_CODES[value]
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError(f"未対応のキー名です: {value}。Linuxの数値キーコードも指定できます")
    code = int(str(value))
    if not 0 <= code <= 767:
        raise ValueError("キーコードは0〜767で指定してください")
    return code


def _seconds(value: object) -> float:
    """連打抑止の秒数を有限の非負値として検証する。"""
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("入力の待ち時間は0以上の秒数で指定してください") from exc
    if isinstance(value, bool) or not math.isfinite(seconds) or not 0 <= seconds <= 60:
        raise ValueError("入力の待ち時間は0〜60秒で指定してください")
    return seconds


@dataclass(frozen=True)
class Binding:
    """1つのキーに割り当てる操作と連打抑止設定。"""

    action: str
    amount: int = 5
    debounce_seconds: float = 0
    quiet_seconds: float = 0


@dataclass(frozen=True)
class InputProfile:
    """同じ製品のUSB・Bluetooth機器に共通して適用する設定。"""

    id: str
    bindings: dict[int, Binding]
    name: str = ""
    exclusive: bool = False


def parse_profiles(raw: str) -> tuple[InputProfile, ...]:
    """JSON化されたptt.devicesを検証し、既定設定と個別割り当てをまとめる。"""
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("ptt.devicesは機器設定の配列で指定してください") from exc
    if not isinstance(items, list):
        raise ValueError("ptt.devicesは機器設定の配列で指定してください")
    profiles: list[InputProfile] = []
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) - {"id", "name", "preset", "exclusive", "bindings"}:
            raise ValueError("機器設定に未対応の項目があります")
        identity = product_id(item.get("id", ""))
        if identity in seen:
            raise ValueError(f"入力機器IDが重複しています: {identity}")
        seen.add(identity)
        preset = item.get("preset", "")
        if not isinstance(preset, str) or preset not in {"", *PRESETS}:
            raise ValueError("入力機器のpresetはmedia_pttまたはmedia_knobです")
        overrides = item.get("bindings", {})
        exclusive = item.get("exclusive", False)
        if not isinstance(overrides, dict) or not isinstance(exclusive, bool) or not isinstance(item.get("name", ""), str):
            raise ValueError("機器のbindingsはテーブル、exclusiveは真偽値、nameは文字列で指定してください")
        bindings: dict[int, Binding] = {}
        # 数値とキー名が同じキーを指していても、個別設定が既定値より優先される。
        for key, spec in [*PRESETS.get(preset, {}).items(), *overrides.items()]:
            code = key_code(key)
            if isinstance(spec, str):
                spec = {"action": spec}
            if not isinstance(spec, dict) or set(spec) - {"action", "amount", "debounce_seconds", "quiet_seconds"}:
                raise ValueError("キー設定に未対応の項目があります")
            action = spec.get("action")
            if not isinstance(action, str) or action not in ACTIONS:
                raise ValueError("キーのactionが未対応です")
            amount = spec.get("amount", 5)
            if type(amount) is not int or not 1 <= amount <= 100:
                raise ValueError("音量のamountは1〜100の整数で指定してください")
            debounce, quiet = (_seconds(spec.get(field, 0)) for field in ("debounce_seconds", "quiet_seconds"))
            if action == "ptt" and (debounce or quiet):
                raise ValueError("pttの押下・解放には連打抑止を指定できません")
            bindings[code] = Binding(action, amount, debounce, quiet)
        if not any(binding.action != "none" for binding in bindings.values()):
            raise ValueError("機器には1つ以上の有効なキー割り当てが必要です")
        profiles.append(InputProfile(identity, bindings, item.get("name", ""), exclusive))
    return tuple(profiles)


def matches_device(path: Path, profiles: tuple[InputProfile, ...], input_root: Path = Path("/sys/class/input")) -> bool:
    """イベント番号に依存せず、製品IDで既に設定済みの機器か調べる。"""
    try:
        device = input_root / path.name / "device/id"
        identity = product_id(f"{(device / 'vendor').read_text().strip()}:{(device / 'product').read_text().strip()}")
    except (OSError, ValueError):
        return False
    return any(profile.id == identity for profile in profiles)


def find_devices(profile: InputProfile, input_root: Path = Path("/sys/class/input")) -> list[Path]:
    """製品IDとキー対応を照合し、複合機器の関連イベントノードを列挙する。"""
    found = []
    for event in sorted(input_root.glob("event*")):
        if not matches_device(event, (profile,), input_root):
            continue
        try:
            bitmap = int("".join(f"{int(word, 16):0{struct.calcsize('l') * 2}x}" for word in (event / "device/capabilities/key").read_text().split()), 16)
        except (OSError, ValueError):
            continue
        if any(bitmap & (1 << key) for key, binding in profile.bindings.items() if binding.action != "none"):
            found.append(Path("/dev/input") / event.name)
    return found


class ProfileInput:
    """設定済みキーを共通処理へ送り、抜き差しや複合入力機器を監視する。"""

    def __init__(self, profile: InputProfile, on_action: Callable[[str, int, int], None],
                 on_disconnect: Callable[[], None], shutdown: threading.Event,
                 clock: Callable[[], float] = time.monotonic, start: bool = True) -> None:
        """機器設定と操作先を保持し、必要なら監視を始める。"""
        self.profile, self._on_action, self._on_disconnect = profile, on_action, on_disconnect
        self._shutdown, self._clock = shutdown, clock
        self._pressed: set[tuple[Path, int]] = set()
        self._accepted: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"input-{profile.id}")
        if start:
            self._thread.start()

    def handle_key(self, path: Path, code: int, value: int) -> None:
        """押下だけを1回扱い、PTTだけは対応する解放も送る。"""
        binding = self.profile.bindings.get(code)
        if binding is None or binding.action == "none" or value not in (0, 1):
            return
        key = (path, code)
        if value == 0:
            if key in self._pressed:
                self._pressed.remove(key)
                # 複数のPTTキーが同時に押されている間は録音を終了しない。
                if binding.action == "ptt" and not any(self.profile.bindings[k].action == "ptt" for _, k in self._pressed):
                    self._on_action(binding.action, 0, binding.amount)
            return
        if key in self._pressed:
            return
        held_ptt = any(self.profile.bindings[k].action == "ptt" for _, k in self._pressed)
        self._pressed.add(key)
        now, action = self._clock(), binding.action
        # スロット送り・戻しは方向を変えても回転が止まるまでまとめる。
        group = "slot" if action in {"slot_next", "slot_previous"} else action
        last_group = self._last.get("group:" + group)
        self._last["group:" + group] = now
        if binding.quiet_seconds and last_group is not None and now - last_group < binding.quiet_seconds:
            return
        if binding.debounce_seconds and now - self._accepted.get(action, -math.inf) < binding.debounce_seconds:
            return
        if action == "ptt" and held_ptt:
            return
        self._accepted[action] = now
        self._on_action(action, 1, binding.amount)

    def _run(self) -> None:
        """関連ノードをまとめて監視し、切断時は録音を安全に取り消す。"""
        opened: dict[Path, int] = {}
        pending: dict[Path, bytes] = {}
        try:
            while not self._shutdown.is_set():
                paths = set(find_devices(self.profile))
                for path in set(opened) - paths:
                    self._close(path, opened, pending)
                for path in paths - set(opened):
                    try:
                        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                        try:
                            if self.profile.exclusive:
                                fcntl.ioctl(fd, EVIOCGRAB, 1)
                        except OSError:
                            os.close(fd)
                            raise
                        opened[path], pending[path] = fd, b""
                    except OSError:
                        log.warning("入力機器を開けません: %s", path, exc_info=True)
                if not opened:
                    self._shutdown.wait(1)
                    continue
                try:
                    readable, _, _ = select.select(list(opened.values()), [], [], 1)
                except OSError:
                    for path in list(opened):
                        self._close(path, opened, pending)
                    self._shutdown.wait(1)
                    continue
                for path, fd in list(opened.items()):
                    if fd not in readable:
                        continue
                    try:
                        chunk = os.read(fd, EVENT.size * 32)
                        if not chunk:
                            self._close(path, opened, pending)
                            continue
                        pending[path] += chunk
                        while len(pending[path]) >= EVENT.size:
                            _, _, kind, code, value = EVENT.unpack_from(pending[path])
                            pending[path] = pending[path][EVENT.size:]
                            if kind == 1:
                                self.handle_key(path, code, value)
                            elif kind == 0 and code == 3:
                                # イベント欠落では押しっぱなしにせず、開き直して同期する。
                                self._close(path, opened, pending)
                                break
                    except OSError:
                        self._close(path, opened, pending)
        finally:
            for path in list(opened):
                self._close(path, opened, pending)

    def _close(self, path: Path, opened: dict[Path, int], pending: dict[Path, bytes]) -> None:
        """切断したノードを解放し、そのノードが押したキーの状態を捨てる。"""
        os.close(opened.pop(path))
        pending.pop(path, None)
        self._pressed = {key for key in self._pressed if key[0] != path}
        self._on_disconnect()
