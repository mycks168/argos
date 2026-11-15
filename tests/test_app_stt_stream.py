"""ARGOS 本体と stt-gateway の /stream（相槌・最初の返事）のつなぎ込みのテスト。"""

from __future__ import annotations

import threading

import pytest
from test_app import _patch_app, _settings

from argos.config import Settings
from argos.core.app import FIRST_REPLY_AGENT_HINT, AgentProgressAnnouncer, ArgosApp, _uses_stt_stream
from argos.hardware.audio import AudioInputStream
from argos.services.stt.stream import SttStreamError


class FakeUtterance:
    """SttStreamUtterance の偽物。"""

    def __init__(self, transcript="ストリーム入力", ack="天気ね、見てみるね。", error=None):
        """返す文字起こし・最初の返事・失敗を保持する。"""
        self.fed: list[tuple[bytes, bool]] = []
        self.transcript = transcript
        self.ack = ack
        self.error = error
        self.failed = False
        self.closed = False
        self.ack_timeouts: list[float] = []

    def feed(self, pcm, *, live=True):
        """送った音声を記録する。"""
        self.fed.append((pcm, live))

    def finish(self, timeout=30.0):
        """文字起こし結果を返すか、失敗を送出する。"""
        if self.error is not None:
            self.failed = True
            raise self.error
        return self.transcript

    def wait_ack(self, timeout):
        """最初の返事を返す。"""
        self.ack_timeouts.append(timeout)
        return self.ack

    def close(self):
        """閉じたことを記録する。"""
        self.closed = True

    def debug_summary(self):
        """調査用の記録を返す。"""
        return {"transcript": self.transcript, "backchannels": []}


class FakeStreamClient:
    """SttStreamClient の偽物。open のたびに用意した発話を返す。"""

    def __init__(self, *utterances):
        """返す発話の列を保持する。"""
        self._utterances = list(utterances)
        self.opened: list[tuple[int, object]] = []

    def open(self, sample_rate, on_backchannel=None):
        """発話を 1 つ取り出して返す。"""
        self.opened.append((sample_rate, on_backchannel))
        return self._utterances.pop(0)


class StreamingRecorder:
    """on_audio を受け付ける録音の偽物。"""

    def __init__(self):
        """録音の状態を初期化する。"""
        self.recording = False
        self.on_audio = None
        self.cancelled = False

    @property
    def is_recording(self):
        """録音中か返す。"""
        return self.recording

    def start(self, on_audio=None):
        """録音を始め、音声の渡し先を覚える。"""
        self.recording = True
        self.on_audio = on_audio

    def stop(self):
        """録音を止めてファイルのパスを返す。"""
        self.recording = False
        return "/tmp/u.wav"

    def cancel(self):
        """録音を破棄する。"""
        self.cancelled = True
        self.recording = False


def _app(monkeypatch, *utterances):
    """ストリーミングを使う ARGOS を作る。"""
    _patch_app(monkeypatch)
    monkeypatch.setattr("argos.core.app.check_audio_level", lambda wav: 100)
    monkeypatch.setattr("argos.core.app.random.choice", lambda phrases: phrases[0])
    app = ArgosApp(_settings())
    app._stt_stream = FakeStreamClient(*utterances)
    app._recorder = StreamingRecorder()
    return app


def test_uses_stt_stream_requires_flag_url_and_real_run():
    """有効化・URL・dry_run でないこと、のすべてがそろったときだけ使う。"""
    base = {**_settings().__dict__, "dry_run": False, "stt_gateway_stream": True}
    assert _uses_stt_stream(Settings(**base))
    assert not _uses_stt_stream(Settings(**{**base, "stt_gateway_url": " "}))
    assert not _uses_stt_stream(Settings(**{**base, "stt_gateway_stream": False}))
    assert not _uses_stt_stream(Settings(**{**base, "dry_run": True}))


def test_audio_input_stream_is_created_for_streaming_without_wakeword(monkeypatch):
    """ウェイクワードを使わなくても、ストリーミングなら共有マイク入力を作る。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    settings = Settings(
        **{**_settings().__dict__, "dry_run": False, "wakeword_enabled": False, "stt_gateway_stream": True}
    )
    stream = app._create_audio_input_stream(settings, ("in",))
    assert isinstance(stream, AudioInputStream)
    # ウェイクワードを使わないので、録音が終わったらマイクを閉じる。
    assert stream._stop_when_idle is True
    with_wakeword = Settings(**{**settings.__dict__, "wakeword_enabled": True})
    assert app._create_audio_input_stream(with_wakeword, ("in",))._stop_when_idle is False
    off = Settings(**{**settings.__dict__, "stt_gateway_stream": False})
    assert app._create_audio_input_stream(off, ("in",)) is None


def test_ptt_press_streams_recording(monkeypatch):
    """PTT の録音を、録りながらストリーミングで送る。"""
    utterance = FakeUtterance()
    app = _app(monkeypatch, utterance)

    app._on_ptt_press()

    assert app._stt_stream.opened[0][0] == 16000
    assert app._stt_stream.opened[0][1] == app._backchannel.submit
    app._recorder.on_audio(b"\x01\x00")
    assert utterance.fed == [(b"\x01\x00", True)]


def test_ptt_press_without_stream_uses_plain_start(monkeypatch):
    """ストリーミングを使わないときは、従来どおり引数なしで録音を始める。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())

    app._on_ptt_press()

    assert app._recorder.started is True


def test_process_recording_uses_stream_transcript_and_ack(monkeypatch, capsys):
    """ストリーミングの文字起こしをエージェントへ渡し、最初の返事を読み上げる。"""
    utterance = FakeUtterance()
    app = _app(monkeypatch, utterance)
    app._on_ptt_press()

    app._process_recording()

    # 最初の返事を読み上げたので、本回答が受け止めを繰り返さないよう補足を付ける。
    assert app._agent.asked == [f"ストリーム入力\n\n{FIRST_REPLY_AGENT_HINT}"]
    assert "天気ね" not in FIRST_REPLY_AGENT_HINT
    assert "ARGOS> 天気ね、見てみるね。" in capsys.readouterr().out
    # ダッシュボードには補足のない元の発話を残す。
    users = [m["text"] for m in app._dashboard_state.snapshot()["messages"] if m.get("role") == "user"]
    assert users[-1] == "ストリーム入力"
    assert utterance.ack_timeouts == [app._settings.stt_gateway_ack_timeout_seconds]
    assert utterance.closed
    assert app._utterance_stream is None


def test_process_recording_falls_back_to_wav_when_stream_fails(monkeypatch, capsys):
    """ストリーミングで失敗したら、録音ファイルで文字起こしし、従来の開始フレーズにする。"""
    utterance = FakeUtterance(error=SttStreamError("down"))
    app = _app(monkeypatch, utterance)
    app._on_ptt_press()

    app._process_recording()

    assert app._agent.asked == ["こんにちは"]
    out = capsys.readouterr().out
    assert "天気ね" not in out
    assert utterance.ack_timeouts == []
    assert utterance.closed


def test_process_recording_without_ack_uses_default_phrase(monkeypatch, capsys):
    """最初の返事が来なければ、従来の開始フレーズを読み上げる。"""
    app = _app(monkeypatch, FakeUtterance(ack=None))
    app._on_ptt_press()

    app._process_recording()

    assert "ARGOS> わかった。少し待ってね。" in capsys.readouterr().out
    # 最初の返事を使っていないので、補足は付けない。
    assert app._agent.asked == ["ストリーム入力"]


def test_process_recording_discards_stream_when_stop_fails(monkeypatch):
    """録音の停止に失敗しても、送りかけの発話を閉じる。"""
    utterance = FakeUtterance()
    app = _app(monkeypatch, utterance)
    app._on_ptt_press()

    def broken_stop():
        """録音の停止に失敗する。"""
        raise RuntimeError("no audio")

    app._recorder.stop = broken_stop

    app._process_recording()

    assert utterance.closed
    assert app._utterance_stream is None


def test_cancel_discards_stream(monkeypatch):
    """キャンセルやマイクOFFで、送りかけの発話を閉じる。"""
    first, second = FakeUtterance(), FakeUtterance()
    app = _app(monkeypatch, first, second)
    app._on_ptt_press()
    app._on_cancel()
    assert first.closed

    app._on_ptt_press()
    app._set_microphone_enabled(False)
    assert second.closed


def test_open_stream_closes_previous_and_survives_errors(monkeypatch):
    """新しい発話を始めるときは前の発話を閉じ、始められなければNoneにする。"""
    first = FakeUtterance()
    app = _app(monkeypatch, first)
    assert app._open_utterance_stream(16000) is first

    def broken_open(sample_rate, on_backchannel=None):
        """発話を始められない。"""
        raise RuntimeError("boom")

    app._stt_stream.open = broken_open
    assert app._open_utterance_stream(16000) is None
    assert first.closed


def test_wakeword_detection_streams_with_pre_roll(monkeypatch):
    """ウェイクワード検知で発話を始め、先読み分は live=False で送る。"""
    utterance = FakeUtterance()
    app = _app(monkeypatch, utterance)
    app._recorder.recording = False
    app._dashboard_state.set_status("ready", "待機中")

    assert app._on_wakeword_detected() is True
    app._on_wakeword_recording_audio(b"pre", pre_roll=True)
    app._on_wakeword_recording_audio(b"live")

    assert app._stt_stream.opened[0][0] == 16000
    assert utterance.fed == [(b"pre", False), (b"live", True)]


def test_wakeword_recording_ready_hands_stream_to_worker(monkeypatch, tmp_path):
    """録音が終わったら、その発話を処理スレッドへ渡す。"""
    utterance = FakeUtterance()
    app = _app(monkeypatch, utterance)
    app._open_utterance_stream(16000)
    started = []

    class FakeThread:
        """起動した処理を記録するスレッドの偽物。"""

        def __init__(self, target, args, daemon):
            """処理と引数を記録する。"""
            started.append((target, args))

        def start(self):
            """何もしない。"""

    monkeypatch.setattr("argos.core.app.threading.Thread", FakeThread)
    app._on_wakeword_recording_ready(str(tmp_path / "w.wav"))

    assert started == [(app._process_wakeword_recording, (str(tmp_path / "w.wav"), False, utterance))]
    assert app._utterance_stream is None


def test_wakeword_recording_ready_closes_stream_when_mic_off(monkeypatch, tmp_path):
    """マイクOFFで録音を捨てるときは、発話も閉じる。"""
    utterance = FakeUtterance()
    app = _app(monkeypatch, utterance)
    app._open_utterance_stream(16000)
    app._microphone_enabled = False

    app._on_wakeword_recording_ready(str(tmp_path / "w.wav"))

    assert utterance.closed


def test_wakeword_processing_uses_stream_transcript_and_ack(monkeypatch, tmp_path, capsys):
    """ウェイクワード後の発話でも、ストリーミングの文字起こしと最初の返事を使う。"""
    wav_path = tmp_path / "wake.wav"
    wav_path.write_bytes(b"dummy")
    utterance = FakeUtterance(transcript="アルゴス、今日の天気は")
    app = _app(monkeypatch, utterance)

    app._process_wakeword_recording(str(wav_path), False, utterance)

    assert app._agent.asked == [f"今日の天気は\n\n{FIRST_REPLY_AGENT_HINT}"]
    assert "ARGOS> 天気ね、見てみるね。" in capsys.readouterr().out
    assert utterance.closed
    assert not wav_path.exists()


def test_can_play_backchannel_only_while_listening(monkeypatch):
    """相槌は、録音中で、ほかの音を鳴らしていないときだけ鳴らす。"""
    app = _app(monkeypatch)
    app._settings = Settings(**{**app._settings.__dict__, "dry_run": False})
    app._audio.is_held = False
    app._dashboard_state.set_status("listening", "録音中")
    assert app._can_play_backchannel()

    app._audio.playing = True
    assert not app._can_play_backchannel()
    app._audio.playing = False

    app._dashboard_state.set_status("thinking", "考え中")
    assert not app._can_play_backchannel()

    app._dashboard_state.set_status("listening", "録音中")
    app._speech.set_muted(True)
    assert not app._can_play_backchannel()


def test_prewarm_backchannel_runs_only_when_streaming(monkeypatch):
    """ストリーミングを使い、相槌を鳴らす設定のときだけ、起動時に合成しておく。"""
    app = _app(monkeypatch)
    called = threading.Event()
    phrases_seen = []
    app._backchannel.prewarm = lambda phrases, speakers: (phrases_seen.append((phrases, speakers)), called.set())

    app._prewarm_backchannel()
    assert not called.wait(0.05)

    app._settings = Settings(**{**app._settings.__dict__, "dry_run": False})
    app._prewarm_backchannel()
    assert called.wait(5)
    # stt-gateway へ送るのと同じ文言を合成しておく。
    assert set(phrases_seen[0][0]) == {"うん", "なるほど", "そっか", "そっかぁ"}
    assert app._settings.voicevox_speaker in phrases_seen[0][1]
    assert all(slot.voicevox_speaker in phrases_seen[0][1] for slot in app._settings.agent_slots
               if slot.voicevox_speaker is not None)

    called.clear()
    app._stt_stream = None
    app._prewarm_backchannel()
    assert not called.wait(0.05)


@pytest.mark.parametrize(
    ("first_reply", "expected"),
    [
        (lambda: "雨ね、調べるね。", "雨ね、調べるね。"),
        (lambda: None, "わかった。少し待ってね。"),
        (lambda: "  ", "わかった。少し待ってね。"),
    ],
)
def test_announcer_prefers_first_reply(monkeypatch, first_reply, expected):
    """開始メッセージには最初の返事を使い、無ければ候補から選ぶ。"""
    spoken = []
    monkeypatch.setattr("argos.core.app.random.choice", lambda phrases: phrases[0])
    announcer = AgentProgressAnnouncer(spoken.append, 10, 10, first_reply=first_reply)

    announcer.start()
    announcer.stop()

    assert spoken == [expected]
    assert announcer.used_first_reply is (expected == "雨ね、調べるね。")


def test_announcer_falls_back_when_first_reply_fails(monkeypatch):
    """最初の返事の取得に失敗しても、候補から選んで読み上げる。"""
    spoken = []
    monkeypatch.setattr("argos.core.app.random.choice", lambda phrases: phrases[0])

    def broken():
        """失敗する取得。"""
        raise RuntimeError("boom")

    announcer = AgentProgressAnnouncer(spoken.append, 10, 10, first_reply=broken)
    announcer.start()
    announcer.stop()

    assert spoken == ["わかった。少し待ってね。"]


def test_debug_recording_is_saved_when_configured(monkeypatch, tmp_path):
    """保存先を設定したときだけ、PTTの録音と記録を残す。"""
    saved = []
    monkeypatch.setattr(
        "argos.core.app.save_debug_recording",
        lambda output_dir, wav_path, summary, kind: saved.append((output_dir, wav_path, summary, kind)),
    )
    app = _app(monkeypatch, FakeUtterance(), FakeUtterance())
    app._on_ptt_press()
    app._process_recording()
    assert saved == []

    app._settings = Settings(**{**app._settings.__dict__, "stt_gateway_debug_recording_dir": str(tmp_path)})
    app._on_ptt_press()
    app._process_recording()
    assert saved[0][0] == str(tmp_path)
    assert saved[0][1] == "/tmp/u.wav"
    assert saved[0][2]["wav_leading_silence_ms"] == 200
    assert saved[0][3] == "ptt"


def test_debug_recording_for_wakeword(monkeypatch, tmp_path):
    """ウェイクワード後の発話も、先頭の無音なしとして残す。"""
    saved = []
    monkeypatch.setattr(
        "argos.core.app.save_debug_recording",
        lambda output_dir, wav_path, summary, kind: saved.append((summary, kind)),
    )
    wav_path = tmp_path / "wake.wav"
    wav_path.write_bytes(b"dummy")
    app = _app(monkeypatch)
    app._settings = Settings(**{**app._settings.__dict__, "stt_gateway_debug_recording_dir": str(tmp_path / "d")})
    utterance = FakeUtterance(transcript="アルゴス、こんにちは")

    app._process_wakeword_recording(str(wav_path), True, utterance)

    assert saved[0][0]["wav_leading_silence_ms"] == 0
    assert saved[0][1] == "followup"


def test_backchannel_block_reasons(monkeypatch):
    """相槌を鳴らせない理由を返す。"""
    app = _app(monkeypatch)
    assert app._backchannel_block_reason() == "dry_run"
    app._settings = Settings(**{**app._settings.__dict__, "dry_run": False})
    app._audio.is_held = True
    assert app._backchannel_block_reason() == "他アプリの音声を優先して保留中"


@pytest.mark.parametrize("progress_voice", [True, False])
def test_stream_client_sends_argos_options(monkeypatch, progress_voice):
    """start に、ARGOS向けの返事の作り方と相槌の設定を付ける。進捗音声がオフなら返事を作らせない。"""
    _patch_app(monkeypatch)
    captured = {}

    class CapturingClient:
        """作られたときの引数を記録する。"""

        def __init__(self, *args, start_options=None, backchannel_start_delay_ms=0, backchannel_fallback_phrases=()):
            """送信指定と互換用の候補を記録する。"""
            captured["options"] = start_options
            captured["delay"] = backchannel_start_delay_ms
            captured["fallback"] = backchannel_fallback_phrases

    monkeypatch.setattr("argos.core.app.SttStreamClient", CapturingClient)
    settings = Settings(
        **{
            **_settings().__dict__,
            "dry_run": False,
            "stt_gateway_stream": True,
            "agent_progress_voice": progress_voice,
            "stt_gateway_backchannel_threshold": 0.4,
            "stt_gateway_backchannel_min_interval_ms": 1200,
            "stt_gateway_backchannel_phrases": ("うん", "へえ"),
            "stt_gateway_backchannel_start_delay_ms": 1800,
        }
    )
    monkeypatch.setattr(ArgosApp, "_create_audio_input_stream", lambda self, settings, devices: None)
    monkeypatch.setattr(ArgosApp, "_create_lcd_display", lambda self, settings: None)
    ArgosApp(settings)

    options = captured["options"]
    assert captured["delay"] == 1800
    assert options["backchannel"] == {
        "threshold": 0.4, "min_interval_ms": 1200,
        "phrases_by_kind": {
            "continuing": "うん", "seeking_agreement": "うん", "opinion": "うん", "step_by_step": "うん",
            "explanation": "なるほど", "sad_or_trouble": ["そっか", "そっかぁ"], "complaint": "そっか", "news": "そっか",
        },
    }
    assert captured["fallback"] == ("うん", "へえ")
    if progress_voice:
        assert options["ack"]["fallback_text"] == "えっと、ちょっと待ってね。"
    else:
        assert options["ack"] is False
