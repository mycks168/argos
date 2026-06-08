"""HDMI ダッシュボードへ表示する状態の管理。"""

from __future__ import annotations

import queue
import threading
import uuid
from collections import deque
from copy import deepcopy
from datetime import datetime
from typing import Any


def _now_iso() -> str:
    """現在時刻をISO 8601形式で返す。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


class DashboardState:
    """会話、通知、ARGOS状態をスレッドセーフに保持する。"""

    def __init__(self, max_messages: int = 60, max_notifications: int = 30) -> None:
        """保持件数と初期状態を設定する。"""
        self._lock = threading.Lock()
        self._messages: deque[dict[str, Any]] = deque(maxlen=max_messages)
        self._notifications: deque[dict[str, Any]] = deque(maxlen=max_notifications)
        self._subscribers: set[queue.Queue[int]] = set()
        self._revision = 0
        self._status = {"code": "ready", "label": "待機中", "updated_at": _now_iso()}
        self._agent = {"name": "", "provider": "", "updated_at": _now_iso()}
        self._audio = {"muted": False, "volume": 0, "updated_at": _now_iso()}

    def snapshot(self) -> dict[str, Any]:
        """現在の表示状態をコピーして返す。"""
        with self._lock:
            return {
                "revision": self._revision,
                "status": deepcopy(self._status),
                "agent": deepcopy(self._agent),
                "audio": deepcopy(self._audio),
                "messages": deepcopy(list(self._messages)),
                "notifications": deepcopy(list(self._notifications)),
            }

    def set_status(self, code: str, label: str) -> None:
        """ARGOSの動作状態を更新する。"""
        with self._lock:
            self._status = {"code": code, "label": label, "updated_at": _now_iso()}
            self._publish_locked()

    def set_agent(self, name: str, provider: str) -> None:
        """現在のエージェントスロット表示を更新する。"""
        with self._lock:
            self._agent = {
                "name": name,
                "provider": provider,
                "updated_at": _now_iso(),
            }
            self._publish_locked()

    def set_audio_muted(self, muted: bool) -> None:
        """音声読み上げのミュート状態を更新する。"""
        with self._lock:
            self._audio = {**self._audio, "muted": muted, "updated_at": _now_iso()}
            self._publish_locked()

    def set_audio_volume(self, volume: int) -> None:
        """音声読み上げの音量表示を更新する。"""
        with self._lock:
            self._audio = {**self._audio, "volume": max(0, min(100, int(volume))), "updated_at": _now_iso()}
            self._publish_locked()

    def add_message(self, role: str, text: str, streaming: bool = False) -> str:
        """会話メッセージを追加し、メッセージIDを返す。"""
        message_id = uuid.uuid4().hex
        with self._lock:
            self._messages.append(
                {
                    "id": message_id,
                    "role": role,
                    "text": text,
                    "streaming": streaming,
                    "created_at": _now_iso(),
                }
            )
            self._publish_locked()
        return message_id

    def append_message(self, message_id: str, delta: str) -> None:
        """指定メッセージへ応答差分を追記する。"""
        if not delta:
            return
        with self._lock:
            message = self._find_message_locked(message_id)
            if message is None:
                return
            message["text"] += delta
            self._publish_locked()

    def finish_message(self, message_id: str) -> None:
        """指定メッセージのストリーミング完了を記録する。"""
        with self._lock:
            message = self._find_message_locked(message_id)
            if message is None:
                return
            message["streaming"] = False
            self._publish_locked()

    def add_notification(
        self,
        title: str,
        text: str,
        source: str = "",
        priority: str = "normal",
        image_url: str = "",
        link_url: str = "",
    ) -> str:
        """外部通知を追加し、通知IDを返す。"""
        notification_id = uuid.uuid4().hex
        with self._lock:
            self._notifications.append(
                {
                    "id": notification_id,
                    "title": title,
                    "text": text,
                    "source": source,
                    "priority": priority,
                    "image_url": image_url,
                    "link_url": link_url,
                    "created_at": _now_iso(),
                }
            )
            self._publish_locked()
        return notification_id

    def add_error_notification(self, source: str, text: str) -> str:
        """内部エラーを通知し、直前と同じ内容なら重複追加しない。"""
        title = f"{source} エラー"
        with self._lock:
            if self._notifications:
                latest = self._notifications[-1]
                if latest["title"] == title and latest["text"] == text and latest["priority"] == "high":
                    return str(latest["id"])
            notification_id = uuid.uuid4().hex
            self._notifications.append(
                {
                    "id": notification_id,
                    "title": title,
                    "text": text,
                    "source": source,
                    "priority": "high",
                    "image_url": "",
                    "link_url": "",
                    "created_at": _now_iso(),
                }
            )
            self._publish_locked()
        return notification_id

    def clear_notifications(self) -> None:
        """外部通知をすべて削除する。"""
        with self._lock:
            self._notifications.clear()
            self._publish_locked()

    def subscribe(self) -> queue.Queue[int]:
        """SSE通知用キューを登録する。"""
        subscriber: queue.Queue[int] = queue.Queue(maxsize=1)
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[int]) -> None:
        """SSE通知用キューを解除する。"""
        with self._lock:
            self._subscribers.discard(subscriber)

    def _find_message_locked(self, message_id: str) -> dict[str, Any] | None:
        """ロック保持中にメッセージIDを検索する。"""
        return next((message for message in self._messages if message["id"] == message_id), None)

    def _publish_locked(self) -> None:
        """状態更新を購読者へ通知する。"""
        self._revision += 1
        for subscriber in self._subscribers:
            try:
                subscriber.put_nowait(self._revision)
            except queue.Full:
                continue
