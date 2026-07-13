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
        self._max_messages = max_messages
        self._current_slot_key = _slot_key("", "")
        self._messages_by_slot: dict[str, deque[dict[str, Any]]] = {
            self._current_slot_key: deque(maxlen=max_messages),
        }
        self._message_slots: dict[str, str] = {}
        self._notifications: deque[dict[str, Any]] = deque(maxlen=max_notifications)
        self._subscribers: set[queue.Queue[int]] = set()
        self._revision = 0
        self._status = {"code": "ready", "label": "待機中", "updated_at": _now_iso()}
        self._agent = {"name": "", "provider": "", "updated_at": _now_iso()}
        self._slots: dict[str, dict[str, Any]] = {}
        self._slot_order: list[str] = []
        self._audio = {"muted": False, "volume": 0, "updated_at": _now_iso()}
        self._microphone = {"enabled": True, "updated_at": _now_iso()}
        self._network = {
            "wifi": {
                "connected": False,
                "interface": "",
                "ssid": "",
                "quality": None,
                "level_dbm": None,
                "updated_at": _now_iso(),
            }
        }
        self._agent_usage: dict[str, dict[str, Any]] = {}
        self._center_alert = {"active": False, "updated_at": _now_iso()}
        self._overlay = {"active": False, "updated_at": _now_iso()}
        self._display_activity = {"sequence": 0, "updated_at": _now_iso()}
        self._slot_stack_center = [{"type": "conversation", "title": "会話", "created_at": _now_iso()}]
        self._slot_stack_right = [{"type": "notifications", "title": "通知", "created_at": _now_iso()}]

    def snapshot(self) -> dict[str, Any]:
        """現在の表示状態をコピーして返す。"""
        with self._lock:
            return {
                "revision": self._revision,
                "status": deepcopy(self._status),
                "agent": deepcopy(self._agent),
                "slots": deepcopy([self._slots[key] for key in self._slot_order if key in self._slots]),
                "agent_usage": {
                    "current": deepcopy(self._agent_usage.get(str(self._agent["provider"]).lower())),
                    "providers": deepcopy(self._agent_usage),
                },
                "audio": deepcopy(self._audio),
                "microphone": deepcopy(self._microphone),
                "network": deepcopy(self._network),
                "center_alert": deepcopy(self._center_alert),
                "overlay": deepcopy(self._overlay),
                "display_activity": deepcopy(self._display_activity),
                "messages": deepcopy(list(self._current_messages_locked())),
                "notifications": deepcopy(list(self._notifications)),
                "slot_stacks": {
                    "center": deepcopy(self._slot_stack_center),
                    "right": deepcopy(self._slot_stack_right),
                },
            }

    def set_status(self, code: str, label: str) -> None:
        """ARGOSの動作状態を更新する。"""
        with self._lock:
            self._status = {"code": code, "label": label, "updated_at": _now_iso()}
            self._publish_locked()

    def status_code(self) -> str:
        """現在の動作状態コードを返す。"""
        with self._lock:
            return str(self._status["code"])

    def set_agent(self, name: str, provider: str, model: str = "") -> None:
        """現在のエージェントスロット表示を更新する。"""
        with self._lock:
            self._current_slot_key = _slot_key(name, provider)
            self._messages_by_slot.setdefault(self._current_slot_key, deque(maxlen=self._max_messages))
            self._ensure_slot_locked(name, provider)
            for slot in self._slots.values():
                slot["active"] = False
            self._slots[self._current_slot_key] = {
                **self._slots[self._current_slot_key],
                "active": True,
                "unread": False,
                "updated_at": _now_iso(),
            }
            self._agent = {
                "name": name,
                "provider": provider,
                "model": model,
                "updated_at": _now_iso(),
            }
            self._publish_locked()

    def set_slots(self, slots: list[tuple[str, str] | tuple[str, str, str]]) -> None:
        """表示するエージェントスロット一覧を設定する。"""
        with self._lock:
            for slot in slots:
                name, provider = slot[:2]
                self._ensure_slot_locked(name, provider)
                model = slot[2] if len(slot) > 2 else ""
                key = _slot_key(name, provider)
                self._slots[key] = {**self._slots[key], "model": model}
            self._publish_locked()

    def set_slot_busy(self, name: str, provider: str, busy: bool) -> None:
        """スロットの処理中状態を更新する。"""
        with self._lock:
            key = _slot_key(name, provider)
            self._ensure_slot_locked(name, provider)
            self._slots[key] = {**self._slots[key], "busy": busy, "updated_at": _now_iso()}
            self._publish_locked()

    def set_slot_unread(self, name: str, provider: str, unread: bool) -> None:
        """スロットの未読応答状態を更新する。"""
        with self._lock:
            key = _slot_key(name, provider)
            self._ensure_slot_locked(name, provider)
            self._slots[key] = {**self._slots[key], "unread": unread, "updated_at": _now_iso()}
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

    def set_microphone_enabled(self, enabled: bool) -> None:
        """マイク入力の受付状態を更新する。"""
        with self._lock:
            self._microphone = {"enabled": bool(enabled), "updated_at": _now_iso()}
            self._publish_locked()

    def set_wifi_status(self, status: dict[str, Any]) -> None:
        """Wi-Fi接続状態を更新する。"""
        with self._lock:
            self._network = {
                **self._network,
                "wifi": {
                    "connected": bool(status.get("connected", False)),
                    "interface": str(status.get("interface", "")),
                    "ssid": str(status.get("ssid", "")),
                    "quality": status.get("quality"),
                    "level_dbm": status.get("level_dbm"),
                    "updated_at": _now_iso(),
                },
            }
            self._publish_locked()

    def set_agent_usage(self, provider: str, usage: dict[str, Any]) -> None:
        """エージェントプロバイダ別の利用枠表示を更新する。"""
        with self._lock:
            normalized = provider.strip().lower()
            self._agent_usage[normalized] = {**deepcopy(usage), "provider": normalized}
            self._publish_locked()

    def has_agent_usage(self, provider: str) -> bool:
        """指定プロバイダの利用枠表示が既にあるか返す。"""
        with self._lock:
            return provider.strip().lower() in self._agent_usage

    def wake_display(self) -> None:
        """音声再生などの利用者向け出力に合わせて画面を起こす。"""
        with self._lock:
            self._display_activity = {
                "sequence": int(self._display_activity["sequence"]) + 1,
                "updated_at": _now_iso(),
            }
            self._publish_locked()

    def add_message(self, role: str, text: str, streaming: bool = False) -> str:
        """会話メッセージを追加し、メッセージIDを返す。"""
        message_id = uuid.uuid4().hex
        with self._lock:
            messages = self._current_messages_locked()
            messages.append(
                {
                    "id": message_id,
                    "role": role,
                    "text": text,
                    "streaming": streaming,
                    "created_at": _now_iso(),
                }
            )
            self._message_slots[message_id] = self._current_slot_key
            self._cleanup_message_slots_locked()
            self._publish_locked()
        return message_id

    def add_message_to_slot(self, name: str, provider: str, role: str, text: str) -> str:
        """指定スロットへ会話メッセージを追加し、メッセージIDを返す。"""
        message_id = uuid.uuid4().hex
        key = _slot_key(name, provider)
        with self._lock:
            self._ensure_slot_locked(name, provider)
            self._messages_by_slot.setdefault(key, deque(maxlen=self._max_messages)).append(
                {
                    "id": message_id,
                    "role": role,
                    "text": text,
                    "streaming": False,
                    "created_at": _now_iso(),
                }
            )
            self._message_slots[message_id] = key
            self._cleanup_message_slots_locked()
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
        display: str = "toast",
        duration_seconds: float = 0.0,
    ) -> str:
        """外部通知を追加し、通知IDを返す。

        display が ``center`` の場合は、右パネルの通知履歴に加えて画面中央の
        大きなアラート表示（center_alert）も更新する。duration_seconds は
        中央アラートの自動消去秒数で、0以下ならタップで閉じるまで残す。
        """
        notification_id = uuid.uuid4().hex
        with self._lock:
            notice = {
                "id": notification_id,
                "title": title,
                "text": text,
                "source": source,
                "priority": priority,
                "image_url": image_url,
                "link_url": link_url,
                "display": display,
                "duration_seconds": duration_seconds,
                "created_at": _now_iso(),
            }
            self._notifications.append(notice)
            if display == "center":
                self._center_alert = {"active": True, **notice, "updated_at": _now_iso()}
            self._publish_locked()
        return notification_id

    def clear_center_alert(self) -> None:
        """画面中央の大きなアラート表示を消去する。"""
        with self._lock:
            self._center_alert = {"active": False, "updated_at": _now_iso()}
            self._publish_locked()

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
                    "display": "toast",
                    "duration_seconds": 0.0,
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
        slot_key = self._message_slots.get(message_id, self._current_slot_key)
        messages = self._messages_by_slot.get(slot_key)
        if messages is None:
            return None
        return next((message for message in messages if message["id"] == message_id), None)

    def set_overlay(
        self,
        overlay_type: str,
        title: str,
        content: str = "",
        url: str = "",
        options: dict[str, Any] | None = None,
        target_slot: str = "right",
    ) -> None:
        """後方互換用のオーバーレイ設定。内部的に push_overlay を呼び出す。"""
        self.push_overlay(target_slot, overlay_type, title, content, url, options)

    def clear_overlay(self, target_slot: str = "all") -> None:
        """後方互換用のオーバーレイ消去。内部的に pop_overlay を呼び出す。"""
        self.pop_overlay(target_slot)

    def push_overlay(
        self,
        target_slot: str,
        overlay_type: str,
        title: str,
        content: str = "",
        url: str = "",
        options: dict[str, Any] | None = None,
        replace_top: bool = False,
    ) -> None:
        """指定したスロットにオーバーレイを表示する。"""
        with self._lock:
            stack = self._slot_stack_center if target_slot == "center" else self._slot_stack_right
            item = {
                "type": overlay_type,
                "title": title,
                "content": content,
                "url": url,
                "options": deepcopy(options) if options is not None else {},
                "created_at": _now_iso(),
            }
            if replace_top and len(stack) > 1:
                stack[-1] = item
            else:
                stack.append(item)
            # 互換用の _overlay を更新
            self._overlay = {
                "active": True,
                "type": overlay_type,
                "title": title,
                "content": content,
                "url": url,
                "options": deepcopy(options) if options is not None else {},
                "updated_at": _now_iso(),
            }
            self._publish_locked()

    def pop_overlay(self, target_slot: str) -> None:
        """指定したスロット（またはすべて）から一時コンテンツを消去（スタックからPop）。"""
        with self._lock:
            slots = ["center", "right"] if target_slot == "all" else [target_slot]
            for s in slots:
                stack = self._slot_stack_center if s == "center" else self._slot_stack_right
                if len(stack) > 1:
                    stack.pop()
            # 互換用 _overlay の更新
            has_active = len(self._slot_stack_center) > 1 or len(self._slot_stack_right) > 1
            if not has_active:
                self._overlay = {
                    "active": False,
                    "updated_at": _now_iso(),
                }
            else:
                active_stack = self._slot_stack_right if len(self._slot_stack_right) > 1 else self._slot_stack_center
                self._overlay = {
                    "active": True,
                    "type": active_stack[-1]["type"],
                    "title": active_stack[-1]["title"],
                    "content": active_stack[-1].get("content", ""),
                    "url": active_stack[-1].get("url", ""),
                    "options": deepcopy(active_stack[-1].get("options", {})),
                    "updated_at": _now_iso(),
                }
            self._publish_locked()

    def swap_slots(self) -> None:
        """左右のスロットの表示内容を入れ替える（スタック全体を交換）。"""
        with self._lock:
            self._slot_stack_center, self._slot_stack_right = self._slot_stack_right, self._slot_stack_center
            # 互換用 _overlay の更新
            has_active = len(self._slot_stack_center) > 1 or len(self._slot_stack_right) > 1
            if has_active:
                active_stack = self._slot_stack_right if len(self._slot_stack_right) > 1 else self._slot_stack_center
                self._overlay = {
                    "active": True,
                    "type": active_stack[-1]["type"],
                    "title": active_stack[-1]["title"],
                    "content": active_stack[-1].get("content", ""),
                    "url": active_stack[-1].get("url", ""),
                    "options": deepcopy(active_stack[-1].get("options", {})),
                    "updated_at": _now_iso(),
                }
            else:
                self._overlay = {"active": False, "updated_at": _now_iso()}
            self._publish_locked()

    def _current_messages_locked(self) -> deque[dict[str, Any]]:
        """現在スロットの会話履歴を返す。"""
        return self._messages_by_slot.setdefault(self._current_slot_key, deque(maxlen=self._max_messages))

    def _cleanup_message_slots_locked(self) -> None:
        """保持上限で消えたメッセージIDの所属情報を削除する。"""
        existing_ids = {message["id"] for messages in self._messages_by_slot.values() for message in messages}
        for message_id in list(self._message_slots):
            if message_id not in existing_ids:
                del self._message_slots[message_id]

    def _ensure_slot_locked(self, name: str, provider: str) -> None:
        """スロット表示情報を必要に応じて作成する。"""
        key = _slot_key(name, provider)
        if key in self._slots:
            return
        self._slots[key] = {
            "key": key,
            "name": name,
            "provider": provider,
            "model": "",
            "active": key == self._current_slot_key,
            "busy": False,
            "unread": False,
            "updated_at": _now_iso(),
        }
        self._slot_order.append(key)

    def _publish_locked(self) -> None:
        """状態更新を購読者へ通知する。"""
        self._revision += 1
        for subscriber in self._subscribers:
            try:
                subscriber.put_nowait(self._revision)
            except queue.Full:
                continue


def _slot_key(name: str, provider: str) -> str:
    """ダッシュボード会話履歴用のスロットキーを作る。"""
    return f"{provider}\0{name}"
