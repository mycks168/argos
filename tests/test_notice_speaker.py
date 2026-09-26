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
        self.texts = []
        self.results = []
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
            play=self._play,
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

    def _play(self, wav, text=""):
        """再生を記録する。resultsに指定があれば、その結果（中断ならFalse）を返す。"""
        self.played.append(wav)
        self.texts.append(text)
        return self.results.pop(0) if self.results else None

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


def test_completion_waits_past_timeout_and_reads_no_body(tmp_path):
    """完了通知は30秒を超えても会話を待ち、本文を読まない。"""
    h = Harness(tmp_path)
    h.busy = [True] * 200
    h.speaker.submit(notice(title="Claude 応答完了", source="ARGOS", text="読まない応答本文"))
    h.speak_next()
    assert h.now == pytest.approx(40)
    assert h.synthesized == [("Claudeの応答が終わったよ。", 1)]
    assert len(h.played) == 1


@pytest.mark.parametrize("cancel", [None, "muted", "locked", "stop"])
def test_completion_rechecks_after_synthesis(tmp_path, cancel):
    """合成中に始まった発話も待ち、ミュート・ロック・停止なら再生しない。"""
    h = Harness(tmp_path)

    def synthesize(text, speaker):
        """音声の合成中に別の発話が始まった状態を作る。"""
        h.busy = [True] * 200
        return voice(speaker, text)

    def sleep(seconds):
        """待機中の状態変更を再現する。"""
        h._sleep(seconds)
        if cancel == "stop":
            h.speaker._stop.set()
        elif cancel:
            setattr(h, cancel, True)

    h.speaker._synthesize = synthesize
    h.speaker._sleep = sleep
    h.speaker.submit(notice(title="Claude 応答完了", source="ARGOS"))
    h.speak_next()
    assert len(h.played) == (1 if cancel is None else 0)
    if cancel is None:
        assert h.now == pytest.approx(40)


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
    h.speaker._play = lambda wav, text="": (h.played.append(wav), spoken.set())
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

    def play(wav, text=""):
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


def test_playback_receives_the_spoken_text_for_echo_defense(tmp_path):
    """再生には、読み上げる文も渡す（ウェイクワードを含むかの判定に使うため）。警告音には空の文を渡す。"""
    h = Harness(tmp_path)
    h.speaker.submit(notice(title="会議"))
    h.speak_next()
    h.fail_synthesis = True
    h.speaker.submit(notice(title="別件"))
    h.speak_next()
    assert h.texts[0].startswith("Slackから通知だよ。会議。") and h.texts[1].startswith("Slackから通知だよ。別件。")


def test_interrupted_playback_is_spoken_again_when_quiet(tmp_path):
    """利用者の発話などで再生を中断されたら、静かになってから、もう一度読み上げる。"""
    h = Harness(tmp_path)
    h.results = [False, None]
    h.speaker.submit(notice(title="会議"))
    h.speak_next()
    assert len(h.played) == 1 and h.speaker._queue.qsize() == 1
    h.busy = [True, True]
    h.speak_next()
    assert len(h.played) == 2 and h.speaker._queue.qsize() == 0 and h.speaker._retries == {}


def test_interrupted_playback_is_retried_only_a_few_times(tmp_path):
    """中断され続けても、読み上げ直すのは最大回数まで。"""
    h = Harness(tmp_path, max_retries=2)
    h.results = [False, False, False, False]
    h.speaker.submit(notice(title="会議"))
    for _ in range(3):
        h.speak_next()
    assert len(h.played) == 3 and h.speaker._queue.qsize() == 0 and h.speaker._retries == {}


def test_retry_is_dropped_when_queue_is_full(tmp_path):
    """順番待ちがいっぱいなら、読み上げ直しは、あきらめる。"""
    h = Harness(tmp_path, max_queue=1)
    h.results = [False]
    h.speaker.submit(notice(title="会議"))
    plan = h.speaker._queue.get_nowait()
    h.speaker.submit(notice(title="別件"))
    h.speaker._speak(plan)
    assert h.speaker._queue.qsize() == 1 and h.speaker._retries == {}


def test_completion_waits_for_whole_conversation_including_thinking(tmp_path):
    """完了通知は、考え中を含む会話が終わるまで待つ。通常の通知は、考え中では待たない。"""
    conversation = [True] * 5
    h = Harness(tmp_path, is_conversation_active=lambda: conversation.pop(0) if conversation else False)
    h.speaker.submit(notice(title="Claude 応答完了", source="ARGOS"))
    h.speak_next()
    assert len(h.played) == 1 and h.now == pytest.approx(1.0)
    h2 = Harness(tmp_path, is_conversation_active=lambda: True)
    h2.speaker.submit(notice(title="会議"))
    h2.speak_next()
    assert len(h2.played) == 1 and h2.now == 0.0


def test_completion_gives_up_after_idle_limit_without_interrupting(tmp_path):
    """会話が上限を超えて終わらないときは、割り込まずに読み上げをやめる（通知欄には残る）。"""
    h = Harness(tmp_path, idle_wait_seconds=10.0)
    h.busy = [True] * 1000
    h.speaker.submit(notice(title="Claude 応答完了", source="ARGOS"))
    h.speak_next()
    assert h.played == [] and 10.0 <= h.now < 10.5
    assert h.speaker._retries == {}


def test_wait_returns_false_when_stopped_during_wait(tmp_path):
    """待っている間に停止されたら、読み上げない。"""
    h = Harness(tmp_path)
    h.busy = [True] * 50
    original = h._sleep

    def stop_then_sleep(seconds):
        """待ちの間に停止を指示する。"""
        h.speaker._stop.set()
        original(seconds)

    h.speaker._sleep = stop_then_sleep
    h.speaker.submit(notice())
    h.speak_next()
    assert h.played == []
