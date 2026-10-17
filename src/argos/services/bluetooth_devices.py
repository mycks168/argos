"""BlueZ が接続中と認識する Bluetooth 機器の種類を取得する。"""

from __future__ import annotations

import re
import subprocess

_DEVICE_LINE = re.compile(r"^Device ([0-9A-Fa-f:]{17})(?:\s|$)")
_ICON_LINE = re.compile(r"^\s*Icon:\s*(\S+)", re.MULTILINE)


def classify_bluetooth_icon(icon: str) -> str:
    """BlueZ の Icon 値を画面表示用の機器種別に変換する。"""
    if icon.startswith("input-keyboard"):
        return "keyboard"
    if icon.startswith("input-mouse") or icon.startswith("input-tablet"):
        return "mouse"
    if icon in {"audio-headset", "audio-headphones"}:
        return "headphones"
    if icon.startswith("audio-"):
        return "speaker"
    return "bluetooth"


def read_connected_bluetooth_types() -> list[str]:
    """接続中の機器を調べ、重複を除いた種類だけを返す。"""
    try:
        result = subprocess.run(
            ["bluetoothctl", "devices", "Connected"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []

    types: list[str] = []
    for line in result.stdout.splitlines():
        match = _DEVICE_LINE.match(line)
        if match is None:
            continue
        try:
            info = subprocess.run(
                ["bluetoothctl", "info", match.group(1)],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            info = None
        icon_match = _ICON_LINE.search(info.stdout) if info is not None and info.returncode == 0 else None
        kind = classify_bluetooth_icon(icon_match.group(1) if icon_match else "")
        if kind not in types:
            types.append(kind)
    return types
