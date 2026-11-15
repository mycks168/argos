"""話している最中に、stt-gateway が予測した相槌を小さめの音量で鳴らす。

相槌は録音を止めずに鳴らす。合成に時間がかかると相槌が遅れて不自然になるため、
合成した音声は話者ごとに覚えておき、よく使う文言は起動時に合成しておく。
"""

from __future__ import annotations

import io
import logging
import random
import threading
import time
import wave
from collections.abc import Callable, Iterable

import numpy as np

# 起動時に合成しておく相槌の既定。stt-gateway へ送る文言の既定と同じにする。
from argos.config import DEFAULT_STT_BACKCHANNEL_PHRASES as DEFAULT_BACKCHANNEL_PHRASES

log = logging.getLogger(__name__)


def scale_wav_volume(wav_data: bytes, factor: float) -> bytes:
    """16bit PCM の WAV の音量を factor 倍にする。16bit 以外はそのまま返す。"""
    if factor >= 1.0:
        return wav_data
    try:
        with wave.open(io.BytesIO(wav_data), "rb") as source:
            params = source.getparams()
            frames = source.readframes(params.nframes)
    except (wave.Error, EOFError):
        return wav_data
    if params.sampwidth != 2:
        return wav_data
    samples = np.frombuffer(frames[: len(frames) - (len(frames) % 2)], dtype="<i2")
    scaled = np.clip(samples.astype(np.float32) * max(0.0, factor), -32768, 32767).astype("<i2")
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as target:
            target.setparams(params)
            target.writeframes(scaled.tobytes())
        return buffer.getvalue()


class BackchannelPlayer:
    """相槌の合成と再生を受け持つ。前の相槌を鳴らしている間に届いた相槌は捨てる。"""

    def __init__(
        self,
        *,
        synthesize: Callable[[str, int], bytes],
        play: Callable[[bytes], object],
        speaker: Callable[[], int],
        can_play: Callable[[], bool],
        volume: float,
        skip_rate: float = 0.0,
        chance: Callable[[], float] = random.random,
    ) -> None:
        """合成・再生・話者の取得・再生してよいかの判定と、音量の倍率、相槌を見送る割合を保持する。

        人は相槌を打てるところで毎回打つわけではないため、届いた相槌の一部（skip_rate）を鳴らさずに見送る。
        chance は 0 以上 1 未満の乱数を返す関数で、テストでは差し替える。
        """
        self._synthesize = synthesize
        self._play = play
        self._speaker = speaker
        self._can_play = can_play
        self._volume = max(0.0, min(1.0, float(volume)))
        self._skip_rate = max(0.0, min(1.0, float(skip_rate)))
        self._chance = chance
        self._cache: dict[tuple[int, str], bytes] = {}
        self._cache_lock = threading.Lock()
        self._busy = threading.Lock()

    @property
    def enabled(self) -> bool:
        """相槌を鳴らす設定ならTrueを返す（音量が0なら鳴らさない）。"""
        return self._volume > 0

    def submit(self, text: str) -> bool:
        """相槌を別スレッドで鳴らし始める。鳴らさなかったときはFalseを返す。"""
        text = text.strip()
        if not text:
            return False
        if not self.enabled:
            log.info("相槌の音量が0のため鳴らしません: %s", text)
            return False
        if not self._can_play():
            log.info("いまは鳴らせないため、相槌を捨てます: %s", text)
            return False
        if self._skip_rate > 0 and self._chance() < self._skip_rate:
            log.info("ときどき相槌を見送るため、鳴らしません: %s", text)
            return False
        if not self._busy.acquire(blocking=False):
            log.info("前の相槌を鳴らしているため、相槌を捨てます: %s", text)
            return False
        threading.Thread(
            target=self._play_in_background, args=(text, time.monotonic()), name="backchannel", daemon=True
        ).start()
        return True

    def prewarm(
        self, phrases: Iterable[str] = DEFAULT_BACKCHANNEL_PHRASES, speakers: Iterable[int] | None = None
    ) -> None:
        """指定された全話者の相槌を合成する。話者の切り替えには影響しない。"""
        if not self.enabled:
            return
        phrases = tuple(dict.fromkeys(phrases))
        for speaker in dict.fromkeys(speakers if speakers is not None else (self._speaker(),)):
            for phrase in phrases:
                try:
                    self._audio_for(phrase, speaker)
                except Exception as exc:
                    log.warning("相槌を事前に合成できませんでした: %s: %s", phrase, exc)
                    return

    def _play_in_background(self, text: str, received_at: float) -> None:
        """合成して、まだ鳴らしてよければ再生する。受け取ってから鳴らすまでの時間をログに出す。"""
        try:
            cached = self._is_cached(text)
            wav_data = self._audio_for(text)
            delay_ms = (time.monotonic() - received_at) * 1000
            if not self._can_play():
                log.info("合成の間に鳴らせない状態になったため、相槌を捨てます: %s", text)
                return
            log.info("相槌: %s (受信から再生開始まで%.0fms、合成済み=%s)", text, delay_ms, cached)
            self._play(wav_data)
        except Exception:
            log.exception("相槌を鳴らせませんでした: %s", text)
        finally:
            self._busy.release()

    def _is_cached(self, text: str) -> bool:
        """いまの話者で、その相槌を合成済みならTrueを返す。"""
        with self._cache_lock:
            return (self._speaker(), text) in self._cache

    def _audio_for(self, text: str, speaker: int | None = None) -> bytes:
        """音量を下げた相槌の音声を返す。合成済みなら覚えたものを使う。"""
        key = (self._speaker() if speaker is None else speaker, text)
        with self._cache_lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        wav_data = scale_wav_volume(self._synthesize(text, key[0]), self._volume)
        with self._cache_lock:
            self._cache[key] = wav_data
        return wav_data
