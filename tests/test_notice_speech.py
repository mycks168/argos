"""通知の読み上げ文の組み立てと、保存音声・警告音のテスト。"""

import io
import wave

import pytest

from argos.services.notice_speech import (
    ERROR_PHRASES,
    PhraseStore,
    SpeechPlan,
    build_error_tone,
    build_plan,
    is_valid_wav,
    should_speak,
)


def make_wav(tag=b"a", frames=8):
    """再生できる、ごく短いWAVを作る。tagで中身を区別できる。"""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(8000)
        wav_file.writeframes((tag * 2)[:2] * frames)
    return buffer.getvalue()


def notice(title="件名", text="本文", source="Slack", priority="normal"):
    """通知1件を作る。"""
    return {"title": title, "text": text, "source": source, "priority": priority}


@pytest.mark.parametrize(
    "source, priority, expected",
    [
        ("Slack", "normal", True),
        ("", "low", True),
        ("ARGOS", "normal", False),
        (" argos ", "normal", False),
        ("ARGOS", "high", True),
        ("Kokoro", "high", True),
    ],
)
def test_should_speak_matches_popup_rule(source, priority, expected):
    """読み上げ対象は、通知欄を自動で開く対象と同じ基準になる。"""
    assert should_speak(notice(source=source, priority=priority)) is expected
    assert should_speak({}) is True


def test_plan_for_external_notice_reads_source_title_and_body():
    """外部の通知は、発信元・題名・本文の順に読み上げる。"""
    plan = build_plan(notice(title="会議のお知らせ", text="3時から", source="Slack"))
    assert plan == SpeechPlan(key="notice:Slack:会議のお知らせ", text="Slackから通知だよ。会議のお知らせ。3時から。", is_error=False)


def test_plan_without_source_or_body():
    """発信元や本文がなくても、読み上げる文を作る。"""
    assert build_plan(notice(source="", text="", title="電話"), 60).text == "通知だよ。電話。"
    assert build_plan(notice(source="", text="", title="")) is None


def test_plan_shortens_urls_newlines_and_long_text():
    """URLは読まず、改行をつめ、長い本文は指定文字数で切って知らせる。"""
    plan = build_plan(notice(text="詳細は https://example.com/a?b=1 を見て\nください" + "あ" * 100), max_chars=20)
    assert "https" not in plan.text and "\n" not in plan.text
    assert "以下省略" in plan.text
    assert "詳細は を見て ください" in plan.text


def test_plan_keeps_terminal_punctuation():
    """本文がすでに句点や感嘆符で終わっていれば、句点を足さない。"""
    assert build_plan(notice(text="行くよ！")).text.endswith("行くよ！")


@pytest.mark.parametrize("source", sorted(ERROR_PHRASES))
def test_plan_for_known_errors_uses_fixed_phrase(source):
    """決まった言葉のあるエラーは、生のエラー文ではなく、その言葉を読み上げる。"""
    plan = build_plan(notice(title=f"{source} エラー", text="Read timed out (host='clove')", source=source, priority="high"))
    assert plan == SpeechPlan(key=f"error:{source}", text=ERROR_PHRASES[source], is_error=True)
    assert "timed out" not in plan.text


@pytest.mark.parametrize("source", ["VOICEVOX", "stt-gateway", "エージェント", "Kokoro"])
def test_plan_ignores_errors_covered_elsewhere(source):
    """代替手段で続いた失敗や、別に読み上げるエラーは、二重に知らせない。"""
    assert build_plan(notice(title=f"{source} エラー", source=source, priority="high")) is None


def test_plan_ignores_argos_internal_notice():
    """ARGOS自身の通常の通知は読み上げない。"""
    assert build_plan(notice(title="ミュート", text="読み上げを一時停止しました。", source="ARGOS")) is None


def test_phrase_store_round_trip_and_speaker_separation(tmp_path):
    """話者ごとに別のファイルへ保存し、保存前はNoneを返す。"""
    store = PhraseStore(tmp_path / "phrases")
    assert store.get("声", 1) is None and not store.has("声", 1)
    assert store.put("声", 1, make_wav(b"a")) is True
    assert store.put("声", 2, make_wav(b"b")) is True
    assert store.get("声", 1) == make_wav(b"a") and store.get("声", 2) == make_wav(b"b")
    assert store.has("声", 1)
    assert not list((tmp_path / "phrases").glob("*.tmp"))


@pytest.mark.parametrize("data", [b"", b"RIFF-broken", "声を作れなかったよ。".encode(), None])
def test_phrase_store_rejects_data_that_cannot_be_played(tmp_path, data):
    """再生できないデータ（空・壊れた・WAVでない）は、保存しない。"""
    store = PhraseStore(tmp_path)
    assert store.put("声", 1, data) is False
    assert store.get("声", 1) is None and not list(tmp_path.iterdir())


def test_phrase_store_treats_a_broken_saved_file_as_missing_and_replaces_it(tmp_path):
    """すでに保存されていた壊れたファイルは、未保存として扱い、作り直したときに置き換える。"""
    store = PhraseStore(tmp_path)
    store._path("声", 1).write_bytes("声を作れなかったよ。".encode())
    assert store.get("声", 1) is None and not store.has("声", 1)
    assert store.put("声", 1, make_wav()) is True
    assert store.get("声", 1) == make_wav()


def test_saved_phrases_are_never_deleted_by_the_store(tmp_path):
    """保存先に、上限や有効期限による自動削除はない。多数を保存しても、古いものが消えない。"""
    store = PhraseStore(tmp_path)
    for index in range(50):
        store.put(f"言葉{index}", 1, make_wav(bytes([65 + index % 20])))
    assert all(store.has(f"言葉{index}", 1) for index in range(50))


@pytest.mark.parametrize("data, expected", [(make_wav(), True), (b"", False), (None, False), (b"RIFFxxxxWAVE", False)])
def test_is_valid_wav(data, expected):
    """再生できるWAVかを判定する。"""
    assert is_valid_wav(data) is expected


def test_is_valid_wav_rejects_wav_without_frames():
    """フレームが0のWAVは、再生できないものとして扱う。"""
    assert is_valid_wav(make_wav(frames=0)) is False


def test_error_tone_is_a_short_valid_wav():
    """警告音は、短い16bit PCMのWAVになる。"""
    with wave.open(io.BytesIO(build_error_tone(16000)), "rb") as wav_file:
        assert wav_file.getnchannels() == 1 and wav_file.getsampwidth() == 2 and wav_file.getframerate() == 16000
        assert 0.3 < wav_file.getnframes() / 16000 < 0.7
        samples = wav_file.readframes(wav_file.getnframes())
    assert any(samples)
