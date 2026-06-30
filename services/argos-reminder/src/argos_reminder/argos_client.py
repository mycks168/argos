"""ARGOSダッシュボードAPIクライアント。"""

from __future__ import annotations

import json
import urllib.request
from urllib.parse import urljoin

from argos_reminder.model import Reminder


class ArgosClient:
    """ARGOSへ通知イベントを送信する。"""

    def __init__(self, dashboard_url: str, token: str) -> None:
        """接続先URLとBearerトークンを設定する。"""
        base_url = dashboard_url.rstrip("/") + "/"
        self._events_url = urljoin(base_url, "api/events")
        self._location_url = urljoin(base_url, "api/location")
        self._token = token

    def send_reminder(self, reminder: Reminder) -> dict[str, object]:
        """リマインダー通知をARGOSへ送信する。"""
        payload = {
            "type": "notification",
            "title": reminder.title,
            "text": reminder.text,
            "source": reminder.source,
            "priority": "normal",
            "sound": reminder.sound,
            "speak": reminder.speak,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(self._events_url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_location(self) -> tuple[float, float] | None:
        """ARGOSの現在地APIから緯度経度を取得する。取得できない場合はNoneを返す。"""
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(self._location_url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            return None
        lat = payload.get("lat")
        lon = payload.get("lon", payload.get("lng"))
        if not isinstance(lat, int | float) or not isinstance(lon, int | float):
            return None
        return float(lat), float(lon)
