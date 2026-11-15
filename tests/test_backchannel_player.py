"""話している最中の相槌を鳴らす部品のテスト。"""

from __future__ import annotations

import io
import threading
import wave

import numpy as np

from argos.core.backchannel_player import BackchannelPlayer, scale_wav_volume


def _wav(value: int = 10000, sampwidth: int = 2) -> bytes:
    """同じ値が続く短い WAV を作る。"""
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(sampwidth)
            target.setframerate(16000)
            if sampwidth == 2:
                target.writeframes(np.full(160, value, dtype="<i2").tobytes())
            else:
                target.writeframes(bytes([128]) * 160)
        return buffer.getvalue()


def _samples(wav_data: bytes) -> np.ndarray:
    """WAV のサンプルを取り出す。"""
    with wave.open(io.BytesIO(wav_data), "rb") as source:
        return np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")


def test_scale_wav_volume_halves_samples():
    """音量を倍率どおりに下げる。"""
    assert int(_samples(scale_wav_volume(_wav(10000), 0.5))[0]) == 5000


def test_scale_wav_volume_keeps_full_volume_and_unknown_data():
    """倍率1以上や、16bit でない・読めない音声はそのまま返す。"""
    data = _wav()
    assert scale_wav_volume(data, 1.0) is data
    eight_bit = _wav(sampwidth=1)
    assert scale_wav_volume(eight_bit, 0.5) == eight_bit
    assert scale_wav_volume(b"not wav", 0.5) == b"not wav"


class Harness:
    """BackchannelPlayer に渡す合成・再生の偽物。"""

    def __init__(self, can_play: bool = True, volume: float = 0.5, skip_rate: float = 0.0, chance=None):
        """呼び出しを記録する入れ物と、再生の完了を知らせる Event を用意する。"""
        self.synthesized: list[tuple[str, int]] = []
        self.played: list[bytes] = []
        self.played_event = threading.Event()
        self.allow = can_play
        self.release = threading.Event()
        self.release.set()
        self.player = BackchannelPlayer(
            synthesize=self.synthesize,
            play=self.play,
            speaker=lambda: 3,
            can_play=lambda: self.allow,
            volume=volume,
            skip_rate=skip_rate,
            **({"chance": chance} if chance is not None else {}),
        )

    def synthesize(self, text: str, speaker: int) -> bytes:
        """合成の呼び出しを記録する。"""
        self.synthesized.append((text, speaker))
        return _wav()

    def play(self, wav_data: bytes) -> bool:
        """再生の呼び出しを記録する。release が立つまで再生中のふりをする。"""
        self.release.wait(5)
        self.played.append(wav_data)
        self.played_event.set()
        return True


def test_submit_plays_scaled_audio_and_caches():
    """相槌を小さめの音量で鳴らし、合成した音声は覚えておく。"""
    harness = Harness()
    assert harness.player.submit(" うん ")
    assert harness.played_event.wait(5)
    assert int(_samples(harness.played[0])[0]) == 5000
    harness.played_event.clear()
    assert harness.player.submit("うん")
    assert harness.played_event.wait(5)
    assert harness.synthesized == [("うん", 3)]


def test_submit_skips_while_previous_backchannel_plays():
    """前の相槌を鳴らしている間に届いた相槌は捨てる。"""
    harness = Harness()
    harness.release.clear()
    assert harness.player.submit("うん")
    assert harness.player.submit("はい") is False
    harness.release.set()
    assert harness.played_event.wait(5)


def test_submit_logs_delay_and_cache(caplog):
    """受け取ってから鳴らすまでの時間と、合成済みだったかをログに出す。"""
    harness = Harness()
    with caplog.at_level("INFO"):
        assert harness.player.submit("うん")
        assert harness.played_event.wait(5)
    assert "合成済み=False" in caplog.text


def test_submit_respects_volume_text_and_can_play():
    """音量0・空文字・鳴らせない状態では鳴らさない。"""
    assert Harness(volume=0).player.submit("うん") is False
    assert Harness().player.submit("  ") is False
    assert Harness(can_play=False).player.submit("うん") is False


def test_background_play_skips_when_state_changed():
    """合成の間に鳴らせない状態になったら鳴らさない。"""
    harness = Harness()

    def synthesize(text, speaker):
        """合成の途中で、鳴らせない状態にする。"""
        harness.allow = False
        return _wav()

    harness.player._synthesize = synthesize
    assert harness.player.submit("うん")
    assert harness.player._busy.acquire(timeout=5)
    harness.player._busy.release()
    assert harness.played == []


def test_background_play_errors_release_lock():
    """合成に失敗しても、次の相槌は鳴らせる。"""
    harness = Harness()

    def synthesize(text, speaker):
        """失敗する合成。"""
        raise RuntimeError("boom")

    harness.player._synthesize = synthesize
    assert harness.player.submit("うん")
    assert harness.player._busy.acquire(timeout=5)
    harness.player._busy.release()


def test_prewarm_synthesizes_phrases_and_stops_on_error():
    """よく使う相槌を合成しておき、失敗したらそこでやめる。"""
    harness = Harness()
    harness.player.prewarm(("うん", "はい"))
    assert harness.synthesized == [("うん", 3), ("はい", 3)]

    calls = []

    def synthesize(text, speaker):
        """失敗する合成。"""
        calls.append(text)
        raise RuntimeError("down")

    failing = Harness()
    failing.player._synthesize = synthesize
    failing.player.prewarm(("うん", "はい"))
    assert calls == ["うん"]

    muted = Harness(volume=0)
    muted.player.prewarm(("うん",))
    assert muted.synthesized == []


def test_prewarm_all_speakers_without_changing_current_speaker():
    """全話者・全候補を重複なく合成し、選択中の話者は変更しない。"""
    harness = Harness()
    harness.player.prewarm(("うん", "そっか", "うん"), (3, 8, 3))
    assert harness.synthesized == [("うん", 3), ("そっか", 3), ("うん", 8), ("そっか", 8)]
    harness.player._audio_for("そっか", 8)
    assert len(harness.synthesized) == 4
    assert harness.player._speaker() == 3


def test_submit_skips_some_backchannels(caplog):
    """乱数が見送る割合を下回ったときは鳴らさず、ログに出す。上回れば鳴らす。"""
    values = iter([0.1, 0.9])
    harness = Harness(skip_rate=0.25, chance=lambda: next(values))
    with caplog.at_level("INFO"):
        assert harness.player.submit("うん") is False
    assert "見送る" in caplog.text
    assert harness.player.submit("うん")
    assert harness.played_event.wait(5)


def test_submit_never_skips_when_rate_is_zero():
    """見送る割合が0なら乱数を使わない。"""
    def chance():
        """呼ばれたら失敗する乱数。"""
        raise AssertionError("乱数を使わない")

    harness = Harness(skip_rate=0.0, chance=chance)
    assert harness.player.submit("うん")
    assert harness.played_event.wait(5)
