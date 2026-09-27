"""`dumpsys notification --noredact` の出力から、表示中の通知を読み取る。

Androidの標準の項目だけを使う。
- 題名(android.title)、本文(android.text / android.bigText)、補足(android.subText)
- 会話名(android.conversationTitle)。Slackならチャンネル名、LINEならグループ名が入る。
- メッセージ一覧(android.messages)。チャット系のアプリ(MessagingStyle)が、送信者と本文を1件ずつ入れる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 表示中の通知の一覧の見出し。この下の段落だけを読む（スヌーズ中や統計の段落は読まない）。
_LIST_HEADER = "  Notification List:"
_RECORD_START = re.compile(r"^ {4}NotificationRecord\(0x[0-9a-f]+: pkg=(\S+)")
_RECORD_KEY = re.compile(r"^ {6}key=(\S+)")
_RECORD_FLAGS = re.compile(r"^ {6}flags=0x([0-9a-f]+)")
_WHEN = re.compile(r"^ {12}when=(\d+)")
_EXTRA_ENTRY = re.compile(r"^ {16}([A-Za-z0-9_.]+)=(.*)$")
_TYPED_VALUE = re.compile(r"^[\w.$]+ \((.*)\)$", re.DOTALL)
_MESSAGE_SPLIT = re.compile(r"\[\d+\] Bundle\[")
_MESSAGE_WITH_SENDER = re.compile(r"(?:^\{|, )sender=(.*?), text=(.*), time=(\d+)\}\]\s*$", re.DOTALL)
_MESSAGE_WITHOUT_SENDER = re.compile(r"text=(.*), time=(\d+)\}\]\s*$", re.DOTALL)

# 常駐する通知（音楽の再生中など）と、まとめ表示用の通知の印。
FLAG_ONGOING_EVENT = 0x2
FLAG_FOREGROUND_SERVICE = 0x40
FLAG_GROUP_SUMMARY = 0x200


@dataclass(frozen=True)
class AndroidMessage:
    """通知に含まれるメッセージ1件。timeはAndroidの時刻（ミリ秒）。"""

    sender: str
    text: str
    time: int


@dataclass(frozen=True)
class AndroidNotification:
    """表示中の通知1件。"""

    package: str
    key: str = ""
    flags: int = 0
    when: int = 0
    title: str = ""
    text: str = ""
    big_text: str = ""
    sub_text: str = ""
    conversation_title: str = ""
    self_name: str = ""
    messages: tuple[AndroidMessage, ...] = field(default_factory=tuple)

    @property
    def is_persistent(self) -> bool:
        """常駐する通知（再生中の表示や、動作中のサービス）か判定する。新着の知らせではないので読まない。"""
        return bool(self.flags & (FLAG_ONGOING_EVENT | FLAG_FOREGROUND_SERVICE))

    @property
    def is_group_summary(self) -> bool:
        """同じアプリの通知をまとめて見せるための通知か判定する。"""
        return bool(self.flags & FLAG_GROUP_SUMMARY)

    @property
    def conversation(self) -> str:
        """会話の名前。会話名がないアプリ（1対1のメッセージや普通の通知）は題名を使う。"""
        return self.conversation_title or self.title

    def all_messages(self) -> tuple[AndroidMessage, ...]:
        """知らせる対象のメッセージを返す。

        メッセージ一覧があればそれを使い、自分が送ったメッセージは除く。一覧がない普通の
        通知は、本文（長い本文があればそちら）を1件のメッセージとして扱う。
        """
        if self.messages:
            return tuple(message for message in self.messages if not self.self_name or message.sender != self.self_name)
        body = self.big_text or self.text
        if not body:
            return ()
        return (AndroidMessage(sender="", text=body, time=self.when),)


def _unwrap(value: str) -> str:
    """`String (値)` の形から値を取り出す。nullは空文字にする。"""
    value = value.strip()
    if value == "null":
        return ""
    match = _TYPED_VALUE.match(value)
    return match.group(1).strip() if match else value


def parse_messages(raw: str) -> tuple[AndroidMessage, ...]:
    """android.messages の値（各メッセージのBundleの並び）を、メッセージの一覧にする。"""
    messages: list[AndroidMessage] = []
    for chunk in _MESSAGE_SPLIT.split(raw)[1:]:
        match = _MESSAGE_WITH_SENDER.search(chunk)
        if match:
            sender, text, time = match.group(1), match.group(2), match.group(3)
        else:
            plain = _MESSAGE_WITHOUT_SENDER.search(chunk)
            if not plain:
                continue
            sender, text, time = "", plain.group(1), plain.group(2)
        sender = "" if sender.strip() == "null" else sender.strip()
        text = text.strip()
        if text and text != "null":
            messages.append(AndroidMessage(sender=sender, text=text, time=int(time)))
    return tuple(messages)


def _parse_record(package: str, lines: list[str]) -> AndroidNotification:
    """通知1件分の行から、必要な項目を取り出す。"""
    key = ""
    flags = 0
    when = 0
    extras: dict[str, str] = {}
    current: str | None = None
    in_notification = False
    in_extras = False
    for line in lines:
        stripped = line.strip()
        if in_extras:
            if re.match(r"^ {12}\}$", line):
                # 最初のextras（本来の通知）だけを読む。公開用の通知(publicNotification)は読まない。
                break
            entry = _EXTRA_ENTRY.match(line)
            if entry:
                current = entry.group(1)
                extras[current] = entry.group(2)
            elif current is not None:
                # 改行を含む値は、次の行へ続く。
                extras[current] += "\n" + line.strip()
            continue
        if stripped == "notification=":
            in_notification = True
            continue
        if in_notification and stripped == "extras={":
            in_extras = True
            continue
        if not key and (match := _RECORD_KEY.match(line)):
            key = match.group(1)
        elif not flags and (match := _RECORD_FLAGS.match(line)):
            flags = int(match.group(1), 16)
        elif in_notification and not when and (match := _WHEN.match(line)):
            when = int(match.group(1))
    return AndroidNotification(
        package=package,
        key=key,
        flags=flags,
        when=when,
        title=_unwrap(extras.get("android.title", "")),
        text=_unwrap(extras.get("android.text", "")),
        big_text=_unwrap(extras.get("android.bigText", "")),
        sub_text=_unwrap(extras.get("android.subText", "")),
        conversation_title=_unwrap(extras.get("android.conversationTitle", "")),
        self_name=_unwrap(extras.get("android.selfDisplayName", "")),
        messages=parse_messages(extras.get("android.messages", "")),
    )


def parse_notifications(text: str) -> list[AndroidNotification]:
    """dumpsysの出力から、表示中の通知の一覧を返す。"""
    records: list[AndroidNotification] = []
    package: str | None = None
    lines: list[str] = []
    in_list = False
    for line in text.splitlines():
        if not in_list:
            in_list = line.rstrip() == _LIST_HEADER
            continue
        if line.startswith("  ") and not line.startswith("   "):
            # 次の段落（2文字下げの見出し）に入ったら、一覧は終わり。
            break
        start = _RECORD_START.match(line)
        if start:
            if package is not None:
                records.append(_parse_record(package, lines))
            package = start.group(1)
            lines = [line]
        elif package is not None:
            lines.append(line)
    if package is not None:
        records.append(_parse_record(package, lines))
    return records
