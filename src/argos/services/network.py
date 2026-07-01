"""Wi-Fi接続状態の取得。"""

from __future__ import annotations

import subprocess
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WifiStatus:
    """ダッシュボードへ表示するWi-Fi状態。"""

    connected: bool
    interface: str
    ssid: str
    quality: int | None
    level_dbm: int | None

    def to_dict(self) -> dict[str, object]:
        """JSON化しやすい辞書へ変換する。"""
        return {
            "connected": self.connected,
            "interface": self.interface,
            "ssid": self.ssid,
            "quality": self.quality,
            "level_dbm": self.level_dbm,
        }


def read_wifi_status(proc_net_wireless: str | Path = "/proc/net/wireless") -> WifiStatus:
    """現在のWi-Fi接続状態を取得する。"""
    wireless = _read_wireless(Path(proc_net_wireless))
    if not wireless:
        return WifiStatus(False, "", "", None, None)
    interface, quality, level_dbm = wireless
    ssid = _read_ssid(interface)
    return WifiStatus(bool(ssid), interface, ssid, quality, level_dbm)


def _read_wireless(path: Path) -> tuple[str, int | None, int | None] | None:
    """`/proc/net/wireless` からインターフェースと電波品質を読む。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines[2:]:
        if ":" not in line:
            continue
        name, values = line.split(":", 1)
        parts = values.split()
        if len(parts) < 3:
            continue
        interface = name.strip()
        quality = _parse_quality(parts[1])
        level_dbm = _parse_level(parts[2])
        return interface, quality, level_dbm
    return None


def _parse_quality(raw: str) -> int | None:
    """`/proc/net/wireless` のlink値を0から100へ丸める。"""
    try:
        link = float(raw.rstrip("."))
    except ValueError:
        return None
    return max(0, min(100, round(link / 70 * 100)))


def _parse_level(raw: str) -> int | None:
    """`/proc/net/wireless` のlevel値をdBmとして読む。"""
    try:
        level = float(raw.rstrip("."))
    except ValueError:
        return None
    if level > 0:
        level -= 256
    return round(level)


def _read_ssid(interface: str) -> str:
    """`iwgetid` から現在接続中のSSIDを読む。"""
    command = _iwgetid_command()
    if not command:
        return ""
    try:
        result = subprocess.run(
            [command, interface, "-r"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _iwgetid_command() -> str:
    """systemdのPATH差異を避けるため、iwgetidの候補パスを返す。"""
    found = shutil.which("iwgetid")
    if found:
        return found
    for candidate in ("/usr/sbin/iwgetid", "/sbin/iwgetid", "/usr/bin/iwgetid"):
        if Path(candidate).exists():
            return candidate
    return ""
