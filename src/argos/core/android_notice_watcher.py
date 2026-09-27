"""Android(Waydroid)の通知を定期的に見て、新しいメッセージをARGOSの通知として知らせる。"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from argos.services.android_notice.parser import AndroidMessage, parse_notifications
from argos.services.android_notice.rules import AndroidApp, MuteRule, is_muted

log = logging.getLogger(__name__)

LXC_ATTACH = ["sudo", "-n", "lxc-attach", "-P", "/var/lib/waydroid/lxc", "-n", "waydroid", "--"]
DUMPSYS_COMMAND = [*LXC_ATTACH, "/system/bin/dumpsys", "notification", "--noredact"]
_URL_PATTERN = re.compile(r"https?://\S+")


def fetch_dumpsys(timeout: float = 8.0) -> str | None:
    """Waydroidから通知の一覧を取り出す。Waydroidが止まっているときなど、失敗したらNoneを返す。"""
    try:
        result = subprocess.run(DUMPSYS_COMMAND, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def clean_text(text: str) -> str:
    """読み上げ向けに、URLを除き、改行や連続する空白を1つの空白にする。"""
    return re.sub(r"\s+", " ", _URL_PATTERN.sub("", text)).strip()


class AndroidNoticeWatcher:
    """Androidの通知を見張り、新しいメッセージを、会話ごとにまとめて通知欄へ出す。

    - 対象のアプリ（apps）の通知だけを扱う。常駐する通知（再生中の表示など）は扱わない。
    - 起動して最初に見えた通知は、すでに届いていたものとして、知らせない。
    - 同じメッセージは1回だけ知らせる（まとめ用の通知と個別の通知に同じ内容が載っていても1回）。
    - ミュートの条件に当てはまるメッセージは知らせない。
    - 本文が summarize_min_chars 文字を超えたら要約し、要約を読み上げる。画面には元の本文を出す。
    通知の取得、要約、通知欄への追加は外から渡すので、実機なしで動作を検証できる。
    """

    def __init__(
        self,
        *,
        enabled: bool,
        apps: tuple[AndroidApp, ...],
        mute_rules: tuple[MuteRule, ...] = (),
        post: Callable[[dict[str, Any]], Any],
        fetch: Callable[[], str | None] = fetch_dumpsys,
        summarize: Callable[[str], str | None] | None = None,
        summarize_min_chars: int = 40,
        interval_seconds: float = 5.0,
        max_remembered: int = 2000,
    ) -> None:
        """対象のアプリ、ミュートの条件、取得・要約・追加の処理と、確認の間隔を保持する。"""
        self._enabled = enabled
        self._apps = {app.package: app for app in apps}
        self._mute_rules = mute_rules
        self._post = post
        self._fetch = fetch
        self._summarize = summarize
        self._summarize_min_chars = summarize_min_chars
        self._interval = max(1.0, interval_seconds)
        self._max_remembered = max(1, max_remembered)
        self._seen: OrderedDict[tuple[str, int, str], None] = OrderedDict()
        self._primed = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        """通知の見張りが有効ならTrueを返す。"""
        return self._enabled and bool(self._apps)

    def start(self) -> None:
        """見張りのスレッドを開始する。無効な設定では何もしない。"""
        if not self.enabled or (self._thread is not None and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="android-notice", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """見張りのスレッドを止める。"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        """一定の間隔で通知を確認する。1回の失敗で見張りを止めない。"""
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception:  # noqa: BLE001 - 想定外の出力などで、見張りそのものを止めない
                log.exception("Androidの通知の確認に失敗しました")
            self._stop.wait(self._interval)

    def poll(self) -> list[dict[str, Any]]:
        """通知を1回確認し、通知欄へ出した通知の内容を返す。"""
        raw = self._fetch()
        if raw is None:
            return []
        groups = self._collect_new_messages(raw)
        if not self._primed:
            # 起動前から出ていた通知は、まとめて読むと長くうるさいので、知らせない。
            self._primed = True
            return []
        posted: list[dict[str, Any]] = []
        for (package, conversation), messages in groups.items():
            notice = self._build_notice(self._apps[package], conversation, messages)
            self._post(notice)
            posted.append(notice)
        return posted

    def _collect_new_messages(self, raw: str) -> dict[tuple[str, str], list[AndroidMessage]]:
        """まだ知らせていないメッセージを、アプリと会話ごとにまとめる。ミュート対象は除く。"""
        groups: dict[tuple[str, str], list[AndroidMessage]] = {}
        for notification in parse_notifications(raw):
            app = self._apps.get(notification.package)
            if app is None or notification.is_persistent:
                continue
            if notification.is_group_summary and not notification.messages:
                # 「3件の新着」のような、まとめ表示だけの通知。中身は個別の通知で知らせる。
                continue
            conversation = notification.conversation
            for message in notification.all_messages():
                identity = (notification.package, message.time, message.text)
                if identity in self._seen:
                    continue
                self._remember(identity)
                if is_muted(self._mute_rules, app, conversation, message.sender, message.text):
                    continue
                groups.setdefault((notification.package, conversation), []).append(message)
        return groups

    def _remember(self, identity: tuple[str, int, str]) -> None:
        """知らせたメッセージを覚える。古いものから忘れ、覚える数に上限を設ける。"""
        self._seen[identity] = None
        while len(self._seen) > self._max_remembered:
            self._seen.popitem(last=False)

    def _build_notice(self, app: AndroidApp, conversation: str, messages: list[AndroidMessage]) -> dict[str, Any]:
        """会話1つ分の新しいメッセージから、通知欄に出す内容と読み上げる文を作る。"""
        senders = list(dict.fromkeys(message.sender for message in messages if message.sender))
        other_senders = [sender for sender in senders if sender != conversation]
        title = f"{conversation}: {'、'.join(other_senders)}" if conversation and other_senders else conversation or "、".join(senders)
        if len(senders) > 1:
            text = "\n".join(f"{message.sender}: {message.text}" if message.sender else message.text for message in messages)
        else:
            text = "\n".join(message.text for message in messages)
        spoken_conversation = conversation.lstrip("#").strip()
        if spoken_conversation and other_senders:
            speech_title = f"{spoken_conversation}、{'、'.join(other_senders)}から"
        else:
            speech_title = spoken_conversation or "、".join(senders)
        return {
            "title": title,
            "text": text,
            "source": app.name,
            "speech_title": speech_title,
            "speech_text": self._speech_body(" ".join(message.text for message in messages)),
        }

    def _speech_body(self, text: str) -> str:
        """読み上げる本文を作る。長ければ要約し、要約できなければ元の本文（読み上げ時に先頭だけ読む）にする。"""
        body = clean_text(text)
        if self._summarize is None or len(body) <= self._summarize_min_chars:
            return body
        summary = self._summarize(body)
        return summary or body
