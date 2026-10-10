"""Androidのアプリ通知の読み取り・判定・要約・見張りのテスト。"""

import subprocess

import pytest
import requests

from argos.core import android_notice_watcher as watcher_module
from argos.core.android_notice_watcher import AndroidNoticeWatcher, clean_text, fetch_dumpsys
from argos.services.android_notice.parser import AndroidMessage, AndroidNotification, parse_messages, parse_notifications
from argos.services.android_notice.rules import (
    DEFAULT_APPS,
    AndroidApp,
    MuteRule,
    is_muted,
    parse_apps,
    parse_mute_rules,
)
from argos.services.android_notice.summarizer import OllamaSummarizer

SLACK = AndroidApp(package="com.Slack", name="Slack")


def slack_record(flags="0x10", messages=(("監視 (ボット)", "回線の使用率が高いです。", 1000),), conversation="#監視通知"):
    """Slackの通知1件分のdumpsysの行を作る（メッセージ一覧を持つ形式）。"""
    bundle_lines = [
        f"                  [{index}] Bundle[{{extras=Bundle[{{com.slack.message_user_id=U1}}], "
        f"sender_person=android.app.Person@1, sender={sender}, text={text}, time={time}}}]"
        for index, (sender, text, time) in enumerate(messages)
    ]
    return [
        f"    NotificationRecord(0x0abc: pkg=com.Slack user=UserHandle{{0}} id=1 tag=null importance=4 key=0|com.Slack|1|null|1: Notification(channel=c flags={flags}))",
        "      uid=10156 userId=0",
        "      key=0|com.Slack|1|null|10156",
        f"      flags={flags}",
        "      notification=",
        "            when=5000",
        "            extras={",
        f"                android.title=String ({conversation}: 監視 (ボット))",
        f"                android.conversationTitle=String ({conversation})",
        "                android.subText=String (サンプル社)",
        "                android.text=SpannableString (回線の使用率が高いです。)",
        "                android.selfDisplayName=String (自分)",
        f"                android.messages=Bundle[] ({len(messages)})",
        *bundle_lines,
        "                android.isGroupConversation=Boolean (true)",
        "            }",
        "      publicNotification=",
        "            extras={",
        "                android.title=String (3 つの 件の新着通知)",
        "            }",
    ]


def plain_record(package="com.example.news", flags="0x10", title="ニュース", text="本文です。", big_text=None, when=7000):
    """メッセージ一覧を持たない、普通の通知1件分の行を作る。"""
    lines = [
        f"    NotificationRecord(0x0def: pkg={package} user=UserHandle{{0}} id=2 tag=null importance=3 key=0|{package}|2|null|1: Notification(channel=n))",
        f"      flags={flags}",
        "      notification=",
        f"            when={when}",
        "            extras={",
        f"                android.title=String ({title})",
        f"                android.text=String ({text})",
    ]
    if big_text is not None:
        lines.append(f"                android.bigText=String ({big_text})")
    lines += ["                android.subText=null", "            }"]
    return lines


def dump(*records):
    """通知の一覧を含むdumpsysの出力を作る。一覧のあとに別の段落も付ける。"""
    lines = ["Current Notification Manager state:", "  Notification List:"]
    for record in records:
        lines.extend(record)
    lines += [
        "  mUseAttentionLight=false",
        "  Snoozed notifications:",
        "    NotificationRecord(0x0fff: pkg=com.Slack user=UserHandle{0} id=9 tag=null)",
    ]
    return "\n".join(lines)


# --- 読み取り -------------------------------------------------------------


def test_parse_messaging_notification():
    """チャット系の通知から、会話名・送信者・本文・時刻を読み取る。一覧の外の段落は読まない。"""
    records = parse_notifications(dump(slack_record()))
    assert len(records) == 1
    record = records[0]
    assert record.package == "com.Slack"
    assert record.key == "0|com.Slack|1|null|10156"
    assert record.flags == 0x10
    assert record.when == 5000
    assert record.conversation == "#監視通知"
    assert record.sub_text == "サンプル社"
    assert record.self_name == "自分"
    assert record.messages == (AndroidMessage("監視 (ボット)", "回線の使用率が高いです。", 1000),)


def test_parse_ignores_public_notification_extras():
    """公開用の通知(publicNotification)の題名で、本来の題名を上書きしない。"""
    record = parse_notifications(dump(slack_record()))[0]
    assert record.title == "#監視通知: 監視 (ボット)"


def test_parse_multiline_values():
    """改行を含む本文やメッセージを、続きの行も含めて読み取る。"""
    record = plain_record(text="1行目\n2行目です")
    record = "\n".join(record).splitlines()
    parsed = parse_notifications(dump(record))[0]
    assert parsed.text == "1行目\n2行目です"

    messages = parse_messages("[0] Bundle[{sender=太郎, text=こんにちは\n元気？, time=10}]")
    assert messages == (AndroidMessage("太郎", "こんにちは\n元気？", 10),)


def test_parse_messages_without_sender_and_empty():
    """送信者のないメッセージも読み、本文が空やnullのメッセージは除く。"""
    raw = "[0] Bundle[{text=お知らせ, time=5}] [1] Bundle[{sender=null, text=null, time=6}] [2] Bundle[{broken}]"
    assert parse_messages(raw) == (AndroidMessage("", "お知らせ", 5),)
    assert parse_messages("[0] Bundle[{sender=null, text=だれか, time=7}]") == (AndroidMessage("", "だれか", 7),)


def test_parse_no_list_returns_empty():
    """通知の一覧がない出力では、空の一覧を返す。"""
    assert parse_notifications("") == []
    assert parse_notifications("  Notification List:\n  mUseAttentionLight=false") == []


def test_notification_flags_and_fallbacks():
    """常駐する通知・まとめ表示の判定と、本文だけの通知のメッセージ化を確かめる。"""
    assert AndroidNotification("p", flags=0x2).is_persistent
    assert AndroidNotification("p", flags=0x40).is_persistent
    assert AndroidNotification("p", flags=0x200).is_group_summary
    plain = AndroidNotification("p", title="題名", text="短い", big_text="長い本文", when=3)
    assert plain.conversation == "題名"
    assert plain.all_messages() == (AndroidMessage("", "長い本文", 3),)
    assert AndroidNotification("p").all_messages() == ()
    own = AndroidNotification(
        "p", self_name="自分", messages=(AndroidMessage("自分", "送った", 1), AndroidMessage("相手", "届いた", 2))
    )
    assert own.all_messages() == (AndroidMessage("相手", "届いた", 2),)


# --- 対象アプリとミュート -------------------------------------------------------


def test_parse_apps():
    """対象アプリを設定から作る。空なら既定のSlack、名前がなければパッケージ名を使う。"""
    assert parse_apps("") == DEFAULT_APPS
    assert parse_apps('[{"package": "jp.naver.line.android", "name": "LINE"}, "com.example"]') == (
        AndroidApp("jp.naver.line.android", "LINE"),
        AndroidApp("com.example", "com.example"),
    )


@pytest.mark.parametrize("raw", ["{", '{"package": "x"}', '[{"name": "x"}]', "[1]"])
def test_parse_apps_rejects_invalid(raw):
    """配列でない設定や、packageのない要素はエラーにする。"""
    with pytest.raises(ValueError):
        parse_apps(raw)


def test_parse_mute_rules():
    """ミュートの条件を設定から作る。条件のない要素はエラーにする。"""
    assert parse_mute_rules("") == ()
    assert parse_mute_rules('[{"app": "Slack", "conversation": "#01"}]') == (MuteRule(app="Slack", conversation="#01"),)
    with pytest.raises(ValueError):
        parse_mute_rules("[{}]")
    with pytest.raises(ValueError):
        parse_mute_rules('["Slack"]')


@pytest.mark.parametrize(
    "rule, expected",
    [
        (MuteRule(app="Slack"), True),
        (MuteRule(app="com.Slack"), True),
        (MuteRule(app="LINE"), False),
        (MuteRule(conversation="監視"), True),
        (MuteRule(app="Slack", conversation="別チャンネル"), False),
        (MuteRule(sender="(ボット)"), True),
        (MuteRule(text="使用率"), True),
        (MuteRule(text="SERVER"), True),
        (MuteRule(), False),
    ],
)
def test_mute_rule_matches(rule, expected):
    """指定した項目がすべて当てはまるときだけ、ミュートする。部分一致は大文字小文字を区別しない。"""
    assert rule.matches(SLACK, "#監視通知", "監視 (ボット)", "server 使用率が高い") is expected


def test_is_muted_any_rule():
    """どれか1つの条件に当てはまれば、ミュートする。"""
    rules = (MuteRule(app="LINE"), MuteRule(sender="ボット"))
    assert is_muted(rules, SLACK, "c", "ボット", "t")
    assert not is_muted(rules, SLACK, "c", "人", "t")
    assert not is_muted((), SLACK, "c", "人", "t")


# --- 要約 -------------------------------------------------------------------


class FakeResponse:
    """requestsの応答の代わり。"""

    def __init__(self, payload, error=None):
        """返す内容と、raise_for_statusで出す例外を保持する。"""
        self._payload = payload
        self._error = error

    def raise_for_status(self):
        """指定された例外を出す。"""
        if self._error:
            raise self._error

    def json(self):
        """返す内容を渡す。"""
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_summarizer_sends_request_and_cleans_output():
    """Ollamaへ要約を頼み、出力の1行目から囲みの記号を除いて返す。"""
    calls = []

    def post(url, json, timeout):
        calls.append((url, json, timeout))
        return FakeResponse({"message": {"content": "「回線の使用率が高いよ。」\n補足"}})

    summarizer = OllamaSummarizer(url="http://ollama.example:11434/", model="gemma4:e4b", max_chars=40, timeout=9, post=post)
    assert summarizer.summarize("長い本文") == "回線の使用率が高いよ。"
    url, payload, timeout = calls[0]
    assert url == "http://ollama.example:11434/api/chat"
    assert timeout == 9
    assert payload["model"] == "gemma4:e4b"
    assert payload["stream"] is False
    assert "40文字以内" in payload["messages"][0]["content"]
    assert payload["messages"][1] == {"role": "user", "content": "長い本文"}


@pytest.mark.parametrize(
    "response",
    [
        requests.ConnectionError("down"),
        FakeResponse({}, error=requests.HTTPError("500")),
        FakeResponse(ValueError("not json")),
        FakeResponse({"message": {"content": "  \n "}}),
        FakeResponse({"message": "壊れた形"}),
    ],
)
def test_summarizer_returns_none_on_failure(response):
    """接続できない、エラー、壊れた応答、空の出力のときは、Noneを返す。"""

    def post(url, json, timeout):
        if isinstance(response, Exception):
            raise response
        return response

    assert OllamaSummarizer(url="http://x", model="m", post=post).summarize("本文") is None


# --- 見張り -------------------------------------------------------------------


class Feed:
    """dumpsysの出力を、順番に返す。"""

    def __init__(self, *outputs):
        """返す出力の並びを保持する。"""
        self._outputs = list(outputs)

    def __call__(self):
        """次の出力を返す。尽きたら最後の出力を返し続ける。"""
        return self._outputs.pop(0) if len(self._outputs) > 1 else self._outputs[0]


def make_watcher(feed, **kwargs):
    """通知欄への追加を記録する見張りを作る。"""
    posted = []
    options = {"enabled": True, "apps": (SLACK,), "post": posted.append, "fetch": feed}
    options.update(kwargs)
    return AndroidNoticeWatcher(**options), posted


def test_first_poll_primes_without_posting():
    """起動して最初に見えた通知は知らせず、そのあと届いた新しいメッセージだけを知らせる。"""
    old = slack_record(messages=(("監視 (ボット)", "古い", 1),))
    new = slack_record(messages=(("監視 (ボット)", "古い", 1), ("監視 (ボット)", "新しい", 2)))
    watcher, posted = make_watcher(Feed(dump(old), dump(new)))
    assert watcher.poll() == []
    watcher.poll()
    assert posted == [
        {
            "title": "#監視通知: 監視 (ボット)",
            "text": "新しい",
            "source": "Slack",
            "speech_title": "監視通知、監視 (ボット)から",
            "speech_text": "新しい",
        }
    ]
    # 同じ内容が続いても、2回は知らせない。
    assert watcher.poll() == []


def test_failed_fetch_does_not_prime():
    """取得に失敗しているあいだは、最初の確認を済ませたことにしない。"""
    watcher, posted = make_watcher(Feed(None, dump(slack_record())))
    assert watcher.poll() == []
    assert watcher.poll() == []
    assert posted == []


def test_duplicate_messages_in_summary_and_child_are_posted_once():
    """まとめ用の通知と個別の通知に同じメッセージが載っていても、1回だけ知らせる。"""
    watcher, posted = make_watcher(Feed(dump(), dump(slack_record(flags="0x210"), slack_record())))
    watcher.poll()
    watcher.poll()
    assert len(posted) == 1


def test_skips_other_apps_persistent_and_summary_only():
    """対象外のアプリ、常駐する通知、中身のないまとめ表示は、知らせない。"""
    records = (
        plain_record(package="com.example.news"),
        plain_record(package="com.Slack", flags="0x2", title="接続中"),
        plain_record(package="com.Slack", flags="0x200", title="3件の新着"),
    )
    watcher, posted = make_watcher(Feed(dump(), dump(*records)))
    watcher.poll()
    watcher.poll()
    assert posted == []


def test_plain_notification_of_another_app():
    """メッセージ一覧のない普通の通知も、対象のアプリなら知らせる（題名を会話名として扱う）。"""
    news = AndroidApp("com.example.news", "ニュース")
    watcher, posted = make_watcher(
        Feed(dump(), dump(plain_record(title="速報", text="短い", big_text="詳しい本文"))), apps=(news,)
    )
    watcher.poll()
    watcher.poll()
    assert posted == [{"title": "速報", "text": "詳しい本文", "source": "ニュース", "speech_title": "速報", "speech_text": "詳しい本文"}]


def test_mute_rules_skip_messages():
    """ミュートの条件に当てはまるメッセージは知らせない。当てはまらない会話は知らせる。"""
    muted = slack_record(conversation="#監視通知", messages=(("監視 (ボット)", "うるさい", 1),))
    other = slack_record(conversation="#雑談", messages=(("花子", "こんにちは", 2),))
    watcher, posted = make_watcher(Feed(dump(), dump(muted, other)), mute_rules=(MuteRule(app="Slack", conversation="監視"),))
    watcher.poll()
    watcher.poll()
    assert [notice["title"] for notice in posted] == ["#雑談: 花子"]


def test_multiple_senders_are_grouped_by_conversation():
    """同じ会話の新しいメッセージは、1件の通知にまとめる。送信者が複数なら本文に名前を付ける。"""
    record = slack_record(conversation="#雑談", messages=(("花子", "おはよう", 1), ("太郎", "やあ", 2)))
    watcher, posted = make_watcher(Feed(dump(), dump(record)))
    watcher.poll()
    watcher.poll()
    assert posted[0]["title"] == "#雑談: 花子、太郎"
    assert posted[0]["text"] == "花子: おはよう\n太郎: やあ"
    assert posted[0]["speech_title"] == "雑談、花子、太郎から"
    assert posted[0]["speech_text"] == "おはよう やあ"


def test_direct_message_uses_sender_as_conversation():
    """会話名が送信者と同じ（1対1のメッセージ）なら、題名は送信者だけにする。"""
    notification = AndroidNotification(
        "com.Slack", title="花子", messages=(AndroidMessage("花子", "今どこ？", 1),)
    )
    watcher, posted = make_watcher(lambda: "")
    notice = watcher._build_notice(SLACK, notification.conversation, list(notification.all_messages()))
    assert notice["title"] == "花子"
    assert notice["speech_title"] == "花子"
    notice = watcher._build_notice(SLACK, "", [AndroidMessage("花子", "今どこ？", 1)])
    assert notice["title"] == "花子"
    assert notice["speech_title"] == "花子"


def test_long_text_is_summarized():
    """指定の文字数を超える本文だけを要約し、要約できなければ元の本文を読む。URLと改行は除く。"""
    long_text = "監視システムの障害です。" * 5 + " https://example.com/x"
    summaries = []

    def summarize(text):
        summaries.append(text)
        return "障害が起きたよ。" if len(summaries) == 1 else None

    watcher, _ = make_watcher(lambda: "", summarize=summarize, summarize_min_chars=40)
    assert watcher._speech_body("短い本文") == "短い本文"
    assert watcher._speech_body(long_text) == "障害が起きたよ。"
    assert summaries[0] == ("監視システムの障害です。" * 5).strip()
    assert watcher._speech_body(long_text) == ("監視システムの障害です。" * 5).strip()
    assert clean_text("a\n\n b https://x.y/z ") == "a b"


def test_remembered_messages_are_bounded():
    """覚えておくメッセージの数に上限を設け、古いものから忘れる。"""
    watcher, _ = make_watcher(lambda: "", max_remembered=2)
    for index in range(3):
        watcher._remember(("p", index, "t"))
    assert list(watcher._seen) == [("p", 1, "t"), ("p", 2, "t")]


def test_start_and_stop_thread():
    """有効なら見張りのスレッドを動かし、止められる。無効や対象アプリなしなら動かさない。"""
    calls = []

    def fetch():
        calls.append(1)
        raise RuntimeError("想定外")

    watcher, _ = make_watcher(fetch, interval_seconds=1)
    watcher.start()
    watcher.start()
    watcher.stop()
    assert calls
    assert watcher.enabled

    disabled, _ = make_watcher(fetch, enabled=False)
    disabled.start()
    assert disabled._thread is None
    assert not make_watcher(fetch, apps=())[0].enabled


def test_fetch_dumpsys(monkeypatch):
    """dumpsysの出力を返し、失敗や時間切れならNoneを返す。"""

    def run_ok(command, **kwargs):
        assert command[-3:] == ["/system/bin/dumpsys", "notification", "--noredact"]
        return subprocess.CompletedProcess(command, 0, stdout="出力", stderr="")

    monkeypatch.setattr(watcher_module.subprocess, "run", run_ok)
    assert fetch_dumpsys() == "出力"
    monkeypatch.setattr(
        watcher_module.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "")
    )
    assert fetch_dumpsys() is None

    def run_timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(watcher_module.subprocess, "run", run_timeout)
    assert fetch_dumpsys() is None
