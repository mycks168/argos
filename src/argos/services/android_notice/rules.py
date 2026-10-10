"""どのアプリの通知を知らせるか、どの通知を読まないか（ミュート）を決める。

アプリ専用の概念（Slackのチャンネルなど）は持たず、どのアプリにもある
「アプリ・会話・送信者・本文」の4つで判定する。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AndroidApp:
    """知らせる対象のアプリ。packageはAndroidのパッケージ名、nameは読み上げと表示に使う名前。"""

    package: str
    name: str


@dataclass(frozen=True)
class MuteRule:
    """読まない通知の条件。指定した項目がすべて当てはまれば読まない。

    appはパッケージ名かアプリの名前と完全一致。conversation・sender・textは部分一致で、
    大文字と小文字を区別しない。
    """

    app: str = ""
    conversation: str = ""
    sender: str = ""
    text: str = ""

    @property
    def is_empty(self) -> bool:
        """条件が1つもない（すべてを読まなくなってしまう）ルールか判定する。"""
        return not (self.app or self.conversation or self.sender or self.text)

    def matches(self, app: AndroidApp, conversation: str, sender: str, text: str) -> bool:
        """通知1件（メッセージ1件）がこのルールに当てはまるか判定する。"""
        if self.is_empty:
            return False
        if self.app and self.app not in (app.package, app.name):
            return False
        for pattern, value in ((self.conversation, conversation), (self.sender, sender), (self.text, text)):
            if pattern and pattern.casefold() not in value.casefold():
                return False
        return True


DEFAULT_APPS = (AndroidApp(package="com.Slack", name="Slack"),)


def parse_apps(raw: str) -> tuple[AndroidApp, ...]:
    """設定（JSONの配列）から対象アプリの一覧を作る。空なら既定（Slack）を使う。"""
    if not raw.strip():
        return DEFAULT_APPS
    payload = _load_list(raw, "notice.android.apps")
    apps: list[AndroidApp] = []
    for item in payload:
        if isinstance(item, str):
            item = {"package": item}
        if not isinstance(item, dict) or not str(item.get("package", "")).strip():
            raise ValueError("notice.android.appsの各要素にはpackageが必要です")
        package = str(item["package"]).strip()
        apps.append(AndroidApp(package=package, name=str(item.get("name") or package).strip()))
    return tuple(apps)


def parse_mute_rules(raw: str) -> tuple[MuteRule, ...]:
    """設定（JSONの配列）からミュートの条件の一覧を作る。条件のない要素はエラーにする。"""
    if not raw.strip():
        return ()
    rules: list[MuteRule] = []
    for item in _load_list(raw, "notice.android.mute"):
        if not isinstance(item, dict):
            raise ValueError("notice.android.muteの各要素はテーブルで指定してください")
        rule = MuteRule(**{name: str(item.get(name) or "").strip() for name in ("app", "conversation", "sender", "text")})
        if rule.is_empty:
            raise ValueError("notice.android.muteの各要素には、app・conversation・sender・textのどれかが必要です")
        rules.append(rule)
    return tuple(rules)


def _load_list(raw: str, name: str) -> list[Any]:
    """JSONの配列を読み込む。配列でなければエラーにする。"""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name}はYAMLの配列で指定してください") from exc
    if not isinstance(payload, list):
        raise ValueError(f"{name}はYAMLの配列で指定してください")
    return payload


def is_muted(rules: tuple[MuteRule, ...], app: AndroidApp, conversation: str, sender: str, text: str) -> bool:
    """どれかのミュートの条件に当てはまるか判定する。"""
    return any(rule.matches(app, conversation, sender, text) for rule in rules)
