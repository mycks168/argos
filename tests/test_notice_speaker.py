"""通知の読み上げ（順番待ち・重ならない待機・失敗時の警告音・保存音声）のテスト。"""

import threading
import time

import pytest

from argos.core.notice_speaker import NoticeSpeaker
import io
import wave

from argos.services.notice_speech import ERROR_PHRASES, PhraseStore


def voice(speaker, text):
    """話者と文言ごとに違う、再生できる短い音声を作る。"""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(8000)
        wav_file.writeframes(f"{speaker}:{text}".encode() + b"\x00")
    return buffer.getvalue()


class Harness:
    """実機なしで動かす読み上げ環境。"""

    def __init__(self, tmp_path, **overrides):
        """生成・再生・状態を偽物にした読み上げ部品を作る。"""
        self.played = []
        self.synthesized = []
        self.now = 0.0
        self.muted = False
        self.busy = []
        self.locked = False
        self.fail_synthesis = False
        self.store = PhraseStore(tmp_path / "phrases")
        arguments = dict(
            enabled=True,
            synthesize=self._synthesize,
            play=self.played.append,
            store=self.store,
            speakers=lambda: {1, 2},
            current_speaker=lambda: 1,
            is_muted=lambda: self.muted,
            is_busy=lambda: self.busy.pop(0) if self.busy else False,
            is_locked=lambda: self.locked,
            min_interval=60.0,
            clock=lambda: self.now,
            sleep=self._sleep,
        )
        arguments.update(overrides)
        self.speaker = NoticeSpeaker(**arguments)

    def _synthesize(self, text, speaker):
        """呼び出しを記録し、失敗を指定できる音声合成。"""
        self.synthesized.append((text, speaker))
        if self.fail_synthesis:
            raise RuntimeError("合成できません")
        return voice(speaker, text)

    def _sleep(self, seconds):
        """待ちの間、時計を進める。"""
        self.now += seconds

    def speak_next(self):
        """順番待ちの先頭を1件、読み上げる。"""
        self.speaker._speak(self.speaker._queue.get_nowait())


def notice(title="件名", source="Slack", priority="normal", text="本文"):
    """通知1件を作る。"""
    return {"title": title, "text": text, "source": source, "priority": priority}


def error(source):
    """内部エラー通知を作る。"""
    return notice(title=f"{source} エラー", source=source, priority="high", text="生のエラー文")


def test_submit_accepts_only_targets_and_skips_repeats(tmp_path):
    """対象の通知だけを受け付け、同じ内容は最小間隔のあいだ受け付けない。"""
    h = Harness(tmp_path)
    assert h.speaker.submit(notice(source="ARGOS")) is False
    assert h.speaker.submit(notice()) is True
    assert h.speaker.submit(notice()) is False
    h.now = 61.0
    assert h.speaker.submit(notice()) is True
    assert h.speaker.submit(notice(title="別の件名")) is True


def test_disabled_speaker_accepts_nothing(tmp_path):
    """無効な設定では、何も受け付けず、スレッドも作らない。"""
    h = Harness(tmp_path, enabled=False)
    assert h.speaker.submit(notice()) is False
    h.speaker.start()
    assert h.speaker._thread is None and h.speaker.enabled is False
    assert h.speaker.warm() is False


def test_queue_overflow_drops_oldest(tmp_path):
    """順番待ちがいっぱいなら、一番古いものを捨てて新しいものを残す。"""
    h = Harness(tmp_path, max_queue=2)
    for index in range(4):
        assert h.speaker.submit(notice(title=f"件名{index}")) is True
    titles = []
    while not h.speaker._queue.empty():
        titles.append(h.speaker._queue.get_nowait().text)
    assert len(titles) == 2 and "件名3" in titles[-1]


def test_speaks_with_synthesized_voice(tmp_path):
    """通常の通知は、音声合成した声で読み上げる。"""
    h = Harness(tmp_path)
    h.speaker.submit(notice(title="会議"))
    h.speak_next()
    assert h.played == [voice(1, "Slackから通知だよ。会議。本文。")]


def test_error_uses_saved_voice_without_synthesis(tmp_path):
    """エラーは、保存済みの声があれば、音声合成を使わずにそれを再生する。"""
    h = Harness(tmp_path)
    saved = voice(1, "保存済み")
    h.store.put(ERROR_PHRASES["音声合成"], 1, saved)
    h.fail_synthesis = True
    h.speaker.submit(error("音声合成"))
    h.speak_next()
    assert h.played == [saved] and h.synthesized == []


def test_error_synthesizes_and_saves_when_not_saved(tmp_path):
    """保存がなければ合成して読み上げ、次のために保存する。"""
    h = Harness(tmp_path)
    h.speaker.submit(error("文字起こし"))
    h.speak_next()
    assert h.played == [voice(1, ERROR_PHRASES["文字起こし"])]
    assert h.store.get(ERROR_PHRASES["文字起こし"], 1) == h.played[0]


def test_falls_back_to_tone_when_synthesis_is_down(tmp_path):
    """音声合成が使えず保存もなければ、警告音で知らせる（通知でも、エラーでも）。"""
    h = Harness(tmp_path)
    h.fail_synthesis = True
    h.speaker.submit(error("音声合成"))
    h.speaker.submit(notice(title="別件"))
    h.speak_next()
    h.speak_next()
    assert len(h.played) == 2 and h.played[0] == h.played[1]
    assert h.played[0][:4] == b"RIFF" and h.played[0] not in (voice(1, ERROR_PHRASES["音声合成"]), voice(1, "別件"))
    assert h.store.get(ERROR_PHRASES["音声合成"], 1) is None


def test_muted_or_locked_skips_speech(tmp_path):
    """ミュート中は何も読まず、ロック中は通知の中身を読まない。エラーの決まった言葉は読む。"""
    h = Harness(tmp_path)
    h.muted = True
    h.speaker.submit(notice())
    h.speak_next()
    assert h.played == []
    h.muted = False
    h.locked = True
    h.speaker.submit(notice(title="秘密"))
    h.speak_next()
    assert h.played == []
    h.speaker.submit(error("文字起こし"))
    h.speak_next()
    assert len(h.played) == 1


def test_waits_while_busy_then_speaks(tmp_path):
    """発話や録音の最中は待ち、終わったら読み上げる。"""
    h = Harness(tmp_path)
    h.busy = [True, True, True]
    h.speaker.submit(notice())
    h.speak_next()
    assert len(h.played) == 1 and h.now == pytest.approx(0.6)


def test_wait_is_bounded(tmp_path):
    """いつまでも終わらないときは、待ち時間の上限で読み上げる。"""
    h = Harness(tmp_path, busy_wait_seconds=1.0)
    h.busy = [True] * 1000
    h.speaker.submit(notice())
    h.speak_next()
    assert len(h.played) == 1 and 1.0 <= h.now < 1.5


def test_mute_during_wait_cancels(tmp_path):
    """待っている間にミュートされたら、読み上げない。"""
    h = Harness(tmp_path)
    h.busy = [True]
    h.speaker.submit(notice())
    original = h._sleep

    def sleep_then_mute(seconds):
        """待っている間に、ミュートを入れる。"""
        h.muted = True
        original(seconds)

    h.speaker._sleep = sleep_then_mute
    h.speak_next()
    assert h.played == []


def test_warm_saves_all_phrases_for_every_speaker(tmp_path):
    """エラーの決まった言葉を、すべての話者ぶん、保存する。保存済みなら何もしない。"""
    h = Harness(tmp_path)
    assert h.speaker.warm() is True
    for text in set(ERROR_PHRASES.values()):
        for speaker in (1, 2):
            assert h.store.has(text, speaker)
    calls = len(h.synthesized)
    assert h.speaker.warm() is True and len(h.synthesized) == calls


def test_warm_retries_later_when_synthesis_is_down(tmp_path):
    """音声合成が使えない間は見送り、待ち時間のあとで、再挑戦する。"""
    h = Harness(tmp_path, warm_retry_seconds=300.0)
    h.fail_synthesis = True
    assert h.speaker.warm() is False
    assert len(h.synthesized) == 1
    assert h.speaker.warm() is False and len(h.synthesized) == 1
    h.now = 301.0
    h.fail_synthesis = False
    assert h.speaker.warm() is True


def test_warm_only_synthesizes_missing_phrases(tmp_path):
    """すでに保存済みの言葉は、作り直さない。"""
    h = Harness(tmp_path)
    for text in set(ERROR_PHRASES.values()):
        for speaker in (1, 2):
            h.store.put(text, speaker, voice(speaker, "保存済み"))
    assert h.speaker.warm() is True and h.synthesized == []


def test_worker_thread_speaks_and_stops(tmp_path):
    """スレッドが、順番待ちの通知を読み上げ、停止で終わる。"""
    h = Harness(tmp_path, clock=time.monotonic, sleep=time.sleep)
    spoken = threading.Event()
    h.speaker._play = lambda wav: (h.played.append(wav), spoken.set())
    h.speaker.start()
    first = h.speaker._thread
    h.speaker.start()
    assert h.speaker._thread is first
    h.speaker.submit(notice(title="会議"))
    assert spoken.wait(3)
    h.speaker.stop()
    assert not first.is_alive()


def test_worker_survives_speech_error(tmp_path):
    """再生が例外を出しても、スレッドは止まらず、次の通知を読む。"""
    h = Harness(tmp_path, clock=time.monotonic, sleep=time.sleep)
    calls = []
    done = threading.Event()

    def play(wav):
        """1回目は失敗し、2回目で完了を知らせる。"""
        calls.append(wav)
        if len(calls) == 1:
            raise RuntimeError("再生できません")
        done.set()

    h.speaker._play = play
    h.speaker.start()
    h.speaker.submit(notice(title="一件目"))
    h.speaker.submit(notice(title="二件目"))
    assert done.wait(3)
    h.speaker.stop()


def test_broken_saved_voice_is_replaced_by_a_fresh_one(tmp_path):
    """保存されていた音声が壊れていたら、使わず、作り直して置き換える（壊れた音声を鳴らさない）。"""
    h = Harness(tmp_path)
    h.store._path(ERROR_PHRASES["音声合成"], 1).parent.mkdir(parents=True)
    h.store._path(ERROR_PHRASES["音声合成"], 1).write_bytes("声を作れなかったよ。".encode())
    h.speaker.submit(error("音声合成"))
    h.speak_next()
    assert h.played == [voice(1, ERROR_PHRASES["音声合成"])]
    assert h.store.get(ERROR_PHRASES["音声合成"], 1) == h.played[0]


def test_invalid_audio_from_synthesis_falls_back_to_tone(tmp_path):
    """音声合成が再生できないデータを返したら、それを鳴らさず、警告音にする。保存もしない。"""
    h = Harness(tmp_path, synthesize=lambda text, speaker: b"not-a-wav")
    h.speaker.submit(error("音声合成"))
    h.speak_next()
    assert h.played[0][:4] == b"RIFF" and h.store.get(ERROR_PHRASES["音声合成"], 1) is None


def test_warm_treats_invalid_audio_as_failure_and_retries(tmp_path):
    """保存用の合成が再生できないデータを返したら、成功扱いにせず、あとで再挑戦する。"""
    h = Harness(tmp_path, synthesize=lambda text, speaker: b"not-a-wav", warm_retry_seconds=300.0)
    assert h.speaker.warm() is False and h.speaker._warmed is False
    assert not list((tmp_path / "phrases").glob("*.wav")) if (tmp_path / "phrases").exists() else True
