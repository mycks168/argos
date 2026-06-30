"""リマインダーのデータモデル。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import atan2, cos, radians, sin, sqrt
from typing import Any

DEFAULT_LOCATION_RADIUS_M = 100.0


@dataclass(frozen=True)
class Reminder:
    """日時または位置指定通知の1件を表す。"""

    id: str
    scheduled_at: datetime | None
    title: str
    text: str
    source: str
    sound: bool
    speak: bool
    created_at: datetime
    sent_at: datetime | None = None
    kind: str = "time"
    target_lat: float | None = None
    target_lon: float | None = None
    radius_m: float = DEFAULT_LOCATION_RADIUS_M

    def is_due(self, now: datetime, location: tuple[float, float] | None = None) -> bool:
        """現在時刻または現在地で送信対象か返す。"""
        if self.sent_at is not None:
            return False
        if self.kind == "location":
            if location is None or self.target_lat is None or self.target_lon is None:
                return False
            return distance_m(location[0], location[1], self.target_lat, self.target_lon) <= self.radius_m
        return self.scheduled_at is not None and self.scheduled_at <= now

    def mark_sent(self, sent_at: datetime) -> "Reminder":
        """送信済み時刻を設定した新しいインスタンスを返す。"""
        return Reminder(
            id=self.id,
            scheduled_at=self.scheduled_at,
            title=self.title,
            text=self.text,
            source=self.source,
            sound=self.sound,
            speak=self.speak,
            created_at=self.created_at,
            sent_at=sent_at,
            kind=self.kind,
            target_lat=self.target_lat,
            target_lon=self.target_lon,
            radius_m=self.radius_m,
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON保存用の辞書へ変換する。"""
        return {
            "id": self.id,
            "kind": self.kind,
            "scheduled_at": self.scheduled_at.isoformat() if self.scheduled_at else None,
            "title": self.title,
            "text": self.text,
            "source": self.source,
            "sound": self.sound,
            "speak": self.speak,
            "created_at": self.created_at.isoformat(),
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "target_lat": self.target_lat,
            "target_lon": self.target_lon,
            "radius_m": self.radius_m,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Reminder":
        """JSON保存用の辞書から復元する。"""
        sent_raw = data.get("sent_at")
        scheduled_raw = data.get("scheduled_at")
        radius_raw = data.get("radius_m", DEFAULT_LOCATION_RADIUS_M)
        return cls(
            id=str(data["id"]),
            scheduled_at=datetime.fromisoformat(str(scheduled_raw)) if scheduled_raw else None,
            title=str(data["title"]),
            text=str(data.get("text", "")),
            source=str(data.get("source", "Reminder")),
            sound=bool(data.get("sound", True)),
            speak=bool(data.get("speak", True)),
            created_at=datetime.fromisoformat(str(data["created_at"])),
            sent_at=datetime.fromisoformat(str(sent_raw)) if sent_raw else None,
            kind=str(data.get("kind", "time")),
            target_lat=_optional_float(data.get("target_lat")),
            target_lon=_optional_float(data.get("target_lon")),
            radius_m=float(radius_raw),
        )


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """2地点間の距離をメートルで返す。"""
    earth_radius_m = 6_371_000.0
    phi1 = radians(lat1)
    phi2 = radians(lat2)
    delta_phi = radians(lat2 - lat1)
    delta_lambda = radians(lon2 - lon1)
    a = sin(delta_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(delta_lambda / 2) ** 2
    return earth_radius_m * 2 * atan2(sqrt(a), sqrt(1 - a))


def _optional_float(value: object) -> float | None:
    """Noneまたは空文字ならNone、それ以外はfloatへ変換する。"""
    if value is None or value == "":
        return None
    return float(value)
