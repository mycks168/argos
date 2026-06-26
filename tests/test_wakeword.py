import json
import wave

import numpy as np

from argos.services.wakeword.livekit import (
    WakeWordListener,
    _pcm16_frames_to_float,
    _pcm16_rms,
    _pcm16_samples,
    _resample_pcm16,
    load_default_threshold,
)
from argos.services.wakeword.vad import detect_vad_segments, estimate_endpoint_from_vad


def test_load_default_threshold_reads_eval_json(tmp_path):
    """評価JSONがあっても設定値のしきい値を優先する。"""
    (tmp_path / "argos_eval.json").write_text(json.dumps({"optimal_threshold": 0.42}), encoding="utf-8")

    assert load_default_threshold(tmp_path, 0.5) == 0.5


def test_load_default_threshold_falls_back(tmp_path):
    """評価JSONがない場合は設定値を使う。"""
    assert load_default_threshold(tmp_path, 0.5) == 0.5


def test_wakeword_listener_writes_score_log(tmp_path):
    """指定されたファイルへ調査用スコアログを書き込む。"""
    log_path = tmp_path / "wakeword-score.log"
    listener = WakeWordListener(
        devices=("mic",),
        model_dir="/tmp/model",
        threshold=0.5,
        score_log_path=str(log_path),
        on_recording_ready=lambda _path: None,
    )

    listener._append_score_log(0.42, 0.5, detected=False)
    listener._append_score_log(0.67, 0.5, detected=True)

    content = log_path.read_text(encoding="utf-8")
    assert "score=0.4200 threshold=0.5000 detected=0" in content
    assert "score=0.6700 threshold=0.5000 detected=1" in content


def test_pcm16_rms_returns_audio_level():
    """PCM16のRMS音量を計算する。"""
    assert _pcm16_rms(b"\x00\x00\x00\x00") == 0.0
    assert _pcm16_rms(b"\x00\x40\x00\xc0") > 0.0


def test_pcm16_frames_to_float_converts_audio():
    """PCM16フレーム列をfloat32音声へ変換する。"""
    samples = _pcm16_frames_to_float([b"\x00\x40\x00\xc0"])

    assert samples.dtype == np.float32
    assert samples.tolist() == [0.5, -0.5]


def test_resample_pcm16_downsamples_48k_to_16k():
    """48kHzのPCM16を16kHzへ落とす。"""
    data = b"".join(int(value).to_bytes(2, "little", signed=True) for value in (0, 3, 6, 9, 12, 15))

    converted = _resample_pcm16(data, 48000, 16000)

    assert _pcm16_samples(converted) == (3, 12)


def test_estimate_endpoint_from_vad_detects_silence():
    """VAD確率列から発話終了位置を推定できる。"""
    probabilities = np.array([0.8] * 5 + [0.1] * 60, dtype=np.float32)

    endpoint = estimate_endpoint_from_vad(
        probabilities,
        threshold=0.5,
        min_seconds=0.1,
        min_silence_seconds=0.2,
    )

    assert endpoint is not None


def test_detect_vad_segments_extracts_speech_ranges():
    """VAD確率列から発話区間を抽出できる。"""
    probabilities = np.array([0.1] * 3 + [0.8] * 10 + [0.1] * 20, dtype=np.float32)

    segments = detect_vad_segments(
        probabilities,
        threshold=0.5,
        min_speech_seconds=0.1,
        min_silence_seconds=0.2,
    )

    assert len(segments) == 1
    assert segments[0].end_seconds > segments[0].start_seconds


def test_wakeword_listener_records_after_detection(monkeypatch):
    """検知直前の音声も含めてWAV化してコールバックへ渡す。"""
    loud = (b"\x00\x40" * 1280)
    silence = (b"\x00\x00" * 1280)
    reads = [loud, loud, silence]
    ready = []

    class FakeStdout:
        def read(self, _size):
            return reads.pop(0) if reads else b""

    class FakeProc:
        stdout = FakeStdout()

        def poll(self):
            return None

        def send_signal(self, _signum):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    class FakeModel:
        def predict_score(self, _waveform):
            return 0.9

    listener = WakeWordListener(
        devices=("mic",),
        model_dir="/tmp/model",
        threshold=0.5,
        window_seconds=0.08,
        interval_seconds=0.0,
        record_min_seconds=0.0,
        record_silence_seconds=0.0,
        min_actual_seconds=0.0,
        endpoint_mode="rms",
        on_recording_ready=lambda path: (ready.append(path), listener.stop()),
    )
    monkeypatch.setattr(listener, "_open_arecord", lambda: FakeProc())

    listener._run_stream(FakeModel(), 0.5)

    assert ready
    assert ready[0].endswith(".wav")
    with wave.open(ready[0], "rb") as wav_file:
        assert wav_file.getnframes() == 1280 * 3


def test_wakeword_listener_clears_buffer_when_detection_ignored(monkeypatch):
    """アプリ側が検知を無視したら残音声バッファを捨てる。"""
    loud = (b"\x00\x40" * 1280)
    reads = [loud, loud, loud, loud]
    ready = []
    detected = 0

    class FakeStdout:
        def read(self, _size):
            return reads.pop(0) if reads else b""

    class FakeProc:
        stdout = FakeStdout()

        def poll(self):
            return None

        def send_signal(self, _signum):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    class FakeModel:
        def predict_score(self, _waveform):
            return 0.9

    def on_detected():
        nonlocal detected
        detected += 1
        if detected == 1:
            return False
        return True

    listener = WakeWordListener(
        devices=("mic",),
        model_dir="/tmp/model",
        threshold=0.5,
        window_seconds=0.08,
        interval_seconds=0.0,
        record_min_seconds=0.0,
        record_silence_seconds=0.0,
        min_actual_seconds=0.0,
        endpoint_mode="rms",
        on_detected=on_detected,
        on_recording_ready=lambda path: (ready.append(path), listener.stop()),
    )
    monkeypatch.setattr(listener, "_open_arecord", lambda: FakeProc())

    listener._run_stream(FakeModel(), 0.5)

    assert detected >= 2
    assert ready
    with wave.open(ready[0], "rb") as wav_file:
        assert wav_file.getnframes() == 1280 * 3


def test_wakeword_listener_records_with_vad(monkeypatch):
    """VAD終了判定でウェイクワード後のWAVを作成できる。"""
    loud = (b"\x00\x40" * 1280)
    silence = (b"\x00\x00" * 1280)
    reads = [loud, loud, silence, silence, silence]
    ready = []

    class FakeStdout:
        def read(self, _size):
            return reads.pop(0) if reads else b""

    class FakeProc:
        stdout = FakeStdout()

        def poll(self):
            return None

        def send_signal(self, _signum):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    class FakeModel:
        def predict_score(self, _waveform):
            return 0.9

    class FakeVadModel:
        def predict(self, samples):
            chunk_count = max(1, int(np.ceil(samples.size / 512)))
            probabilities = np.zeros(chunk_count, dtype=np.float32)
            probabilities[:5] = 0.8
            return probabilities

    listener = WakeWordListener(
        devices=("mic",),
        model_dir="/tmp/model",
        threshold=0.5,
        window_seconds=0.08,
        interval_seconds=0.0,
        record_min_seconds=0.0,
        record_silence_seconds=0.0,
        min_actual_seconds=0.0,
        endpoint_mode="vad",
        vad_min_silence_seconds=0.0,
        vad_check_seconds=0.0,
        on_recording_ready=lambda path: (ready.append(path), listener.stop()),
    )
    monkeypatch.setattr(listener, "_open_arecord", lambda: FakeProc())
    monkeypatch.setattr(listener, "_load_vad_model", lambda: FakeVadModel())

    listener._run_stream(FakeModel(), 0.5)

    assert ready
    with wave.open(ready[0], "rb") as wav_file:
        assert wav_file.getnframes() >= 1280 * 2


def test_wakeword_listener_reports_open_failure(monkeypatch):
    """入力デバイスを開けない場合はエラーにする。"""
    class FakeFailedProc:
        stderr = None

        def poll(self):
            return 1

    monkeypatch.setattr("argos.services.wakeword.livekit.subprocess.Popen", lambda *args, **kwargs: FakeFailedProc())
    listener = WakeWordListener(("missing",), "/tmp/model", 0.5, lambda _path: None)

    try:
        listener._open_arecord()
    except RuntimeError as exc:
        assert "開けません" in str(exc)
    else:
        raise AssertionError("RuntimeErrorが発生しませんでした")
