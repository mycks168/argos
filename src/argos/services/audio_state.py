"""読み上げ音量とミュート状態の永続化。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioState:
    """保存済みの音声状態。"""

    volume: int | None = None
    muted: bool | None = None


class AudioStateStore:
    """音声状態をJSONファイルへ保存する。"""

    def __init__(self, path: str) -> None:
        """保存先パスを初期化する。"""
        self._path = Path(path).expanduser() if path.strip() else None

    def load(self) -> AudioState:
        """保存済み音声状態を読み込む。壊れた値は無視する。"""
        if self._path is None:
            return AudioState()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return AudioState()
        if not isinstance(raw, dict):
            return AudioState()
        volume = _coerce_volume(raw.get("volume"))
        muted = raw.get("muted") if isinstance(raw.get("muted"), bool) else None
        return AudioState(volume=volume, muted=muted)

    def save(self, volume: int, muted: bool) -> None:
        """音量とミュート状態を保存する。"""
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "volume": _clamp_volume(volume),
            "muted": bool(muted),
        }
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(self._path)


def _coerce_volume(value: object) -> int | None:
    """保存値を音量として読み込み、範囲外は補正する。"""
    if isinstance(value, bool):
        return None
    if not isinstance(value, int):
        return None
    return _clamp_volume(value)


def _clamp_volume(value: int) -> int:
    """音量を0から100に丸める。"""
    return max(0, min(100, int(value)))
