"""LiveKit形式ONNXモデルを使ったウェイクワード検知。"""

from __future__ import annotations

import json
import logging
import math
import os
import signal
import struct
import subprocess
import threading
import time
import uuid
import wave
from collections import deque
from collections.abc import Callable, Iterable
from pathlib import Path


SAMPLE_RATE = 16000
EMBEDDING_WINDOW = 76
EMBEDDING_STRIDE = 8
MIN_EMBEDDINGS = 16
log = logging.getLogger(__name__)


class MelSpectrogramFrontend:  # pragma: no cover
    """melspectrogram.onnxで波形をメル特徴量へ変換する。"""

    def __init__(self, onnx_path: str | Path) -> None:
        """ONNX Runtimeセッションを初期化する。"""
        import onnxruntime as ort

        path = Path(onnx_path)
        if not path.exists():
            raise FileNotFoundError(f"メル特徴量モデルが見つかりません: {path}")
        self._session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        self._input_name = self._session.get_inputs()[0].name

    def __call__(self, audio):
        """16kHz float32波形から正規化済みメル特徴量を返す。"""
        import numpy as np

        if audio.ndim == 1:
            audio = audio[np.newaxis, :]
        results = []
        for index in range(audio.shape[0]):
            chunk = audio[index : index + 1].astype(np.float32)
            results.append(self._session.run(None, {self._input_name: chunk})[0])
        mel = np.concatenate(results, axis=0)
        if mel.ndim == 4:
            mel = mel[:, 0, :, :]
        return mel / 10.0 + 2.0


class SpeechEmbedding:  # pragma: no cover
    """embedding_model.onnxでメル特徴量を音声埋め込みへ変換する。"""

    def __init__(self, onnx_path: str | Path) -> None:
        """ONNX Runtimeセッションを初期化する。"""
        import onnxruntime as ort

        path = Path(onnx_path)
        if not path.exists():
            raise FileNotFoundError(f"音声埋め込みモデルが見つかりません: {path}")
        self._session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        self._input_name = self._session.get_inputs()[0].name

    def __call__(self, mel_windows):
        """メル特徴量ウィンドウから96次元埋め込みを返す。"""
        import numpy as np

        if mel_windows.ndim == 3:
            mel_windows = mel_windows[..., np.newaxis]
        outputs = self._session.run(None, {self._input_name: mel_windows.astype(np.float32)})
        return outputs[0].squeeze(axis=(1, 2))


class LiveKitWakeWordModel:  # pragma: no cover
    """LiveKit wakewordの3段ONNX推論を実行する。"""

    def __init__(
        self,
        classifier_path: str | Path,
        mel_path: str | Path,
        embedding_path: str | Path,
    ) -> None:
        """前処理モデルと分類器を読み込む。"""
        import onnxruntime as ort

        classifier = Path(classifier_path)
        if not classifier.exists():
            raise FileNotFoundError(f"ウェイクワード分類器が見つかりません: {classifier}")
        self._mel_frontend = MelSpectrogramFrontend(mel_path)
        self._speech_embedding = SpeechEmbedding(embedding_path)
        self._classifier = ort.InferenceSession(str(classifier), providers=["CPUExecutionProvider"])
        self._classifier_input_name = self._classifier.get_inputs()[0].name

    def predict_score(self, audio_chunk) -> float:
        """16kHz音声チャンクに対する検知スコアを返す。"""
        import numpy as np

        audio = np.asarray(audio_chunk).flatten()
        if audio.dtype == np.int16:
            audio = audio.astype(np.float32) / 32768.0
        else:
            audio = audio.astype(np.float32)

        all_mel = self._mel_frontend(audio)
        if all_mel.ndim == 3:
            all_mel = all_mel[0]
        if all_mel.shape[0] < EMBEDDING_WINDOW:
            return 0.0

        embeddings = []
        for start in range(0, all_mel.shape[0] - EMBEDDING_WINDOW + 1, EMBEDDING_STRIDE):
            window = all_mel[start : start + EMBEDDING_WINDOW]
            embeddings.append(self._speech_embedding(window[None, :, :])[0])
        if len(embeddings) < MIN_EMBEDDINGS:
            return 0.0

        emb_sequence = np.stack(embeddings[-MIN_EMBEDDINGS:], axis=0)
        emb_input = emb_sequence[None, :, :].astype(np.float32)
        outputs = self._classifier.run(None, {self._classifier_input_name: emb_input})
        return float(outputs[0][0, 0])


class WakeWordListener:
    """ALSA入力を常時監視し、検知後の発話WAVをコールバックへ渡す。"""

    def __init__(
        self,
        devices: Iterable[str],
        model_dir: str | Path,
        threshold: float,
        on_recording_ready: Callable[[str], None],
        *,
        on_detected: Callable[[], bool | None] | None = None,
        capture_sample_rate: int = SAMPLE_RATE,
        window_seconds: float = 2.0,
        interval_seconds: float = 0.25,
        chunk_ms: int = 80,
        record_min_seconds: float = 1.0,
        record_max_seconds: float = 12.0,
        record_silence_seconds: float = 1.0,
        pre_roll_seconds: float = 3.0,
        min_actual_seconds: float = 0.4,
        silence_rms_threshold: float = 200.0,
        endpoint_mode: str = "vad",
        vad_model_path: str = "",
        vad_threshold: float = 0.35,
        vad_min_silence_seconds: float = 1.5,
        vad_check_seconds: float = 0.32,
        score_log_path: str = "",
    ) -> None:
        """監視対象デバイスと検知後録音の条件を保持する。"""
        self._devices = tuple(device for device in devices if device)
        if not self._devices:
            raise ValueError("ウェイクワード入力デバイス候補が空です")
        self._model_dir = Path(model_dir).expanduser()
        self._threshold = threshold
        self._capture_sample_rate = max(1, int(capture_sample_rate))
        self._on_recording_ready = on_recording_ready
        self._on_detected = on_detected
        self._window_seconds = window_seconds
        self._interval_seconds = interval_seconds
        self._chunk_ms = chunk_ms
        self._record_min_seconds = record_min_seconds
        self._record_max_seconds = record_max_seconds
        self._record_silence_seconds = record_silence_seconds
        self._pre_roll_seconds = pre_roll_seconds
        self._min_actual_seconds = min_actual_seconds
        self._silence_rms_threshold = silence_rms_threshold
        self._endpoint_mode = endpoint_mode
        self._vad_model_path = vad_model_path
        self._vad_threshold = vad_threshold
        self._vad_min_silence_seconds = vad_min_silence_seconds
        self._vad_check_seconds = vad_check_seconds
        self._score_log_path = Path(score_log_path).expanduser() if score_log_path.strip() else None
        self._vad_model: object | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None

    def start(self) -> None:
        """バックグラウンドで監視を開始する。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """監視を停止する。"""
        self._stop.set()
        self._stop_process()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)

    def _run(self) -> None:
        """モデルを読み込んで、停止要求まで監視を続ける。"""
        try:
            model = LiveKitWakeWordModel(
                classifier_path=self._model_dir / "argos.onnx",
                mel_path=self._model_dir / "melspectrogram.onnx",
                embedding_path=self._model_dir / "embedding_model.onnx",
            )
            threshold = load_default_threshold(self._model_dir, self._threshold)
        except Exception:
            log.exception("ウェイクワードモデルを初期化できません")
            return

        while not self._stop.is_set():
            try:
                self._run_stream(model, threshold)
            except Exception:
                log.exception("ウェイクワード監視が失敗しました。再試行します")
                if self._stop.wait(2.0):
                    return

    def _run_stream(self, model: LiveKitWakeWordModel, threshold: float) -> None:
        """arecordのrawストリームを読みながら検知する。"""
        proc = self._open_arecord()
        self._proc = proc
        chunk_size = max(1, int(self._capture_sample_rate * self._chunk_ms / 1000))
        bytes_per_chunk = chunk_size * 2
        ring_size = max(1, int(SAMPLE_RATE * self._window_seconds))
        ring = deque([0] * ring_size, maxlen=ring_size)
        actual_samples = 0
        pre_roll_chunks = max(1, int(self._pre_roll_seconds * 1000 / self._chunk_ms))
        raw_ring = deque(maxlen=pre_roll_chunks)
        last_predict = 0.0
        last_score_log = time.monotonic()
        best_score_since_log = 0.0
        try:
            while not self._stop.is_set():
                data = proc.stdout.read(bytes_per_chunk) if proc.stdout else b""
                if not data:
                    raise RuntimeError("arecordの音声ストリームが終了しました")
                model_data = _resample_pcm16(data, self._capture_sample_rate, SAMPLE_RATE)
                raw_ring.append(model_data)
                samples = _pcm16_samples(model_data)
                ring.extend(samples)
                actual_samples += len(samples)
                now = time.monotonic()
                if actual_samples < int(SAMPLE_RATE * self._min_actual_seconds) or now - last_predict < self._interval_seconds:
                    continue
                last_predict = now
                score = model.predict_score(_float_waveform(ring))
                best_score_since_log = max(best_score_since_log, score)
                log.debug("ウェイクワードスコア: %.4f threshold=%.4f", score, threshold)
                if now - last_score_log >= 1.0:
                    self._append_score_log(best_score_since_log, threshold, detected=False)
                    best_score_since_log = 0.0
                    last_score_log = now
                if score >= threshold:
                    log.info("ウェイクワード検知: score=%.4f threshold=%.4f", score, threshold)
                    self._append_score_log(score, threshold, detected=True)
                    if self._on_detected is not None:
                        if self._on_detected() is False:
                            log.info("ウェイクワード検知をアプリ側で無視しました")
                            continue
                    wav_path = self._record_utterance(proc, bytes_per_chunk, pre_roll_frames=list(raw_ring))
                    ring.clear()
                    raw_ring.clear()
                    if wav_path:
                        self._on_recording_ready(wav_path)
        finally:
            self._stop_process()

    def _append_score_log(self, score: float, threshold: float, *, detected: bool) -> None:
        """tmpfs上の調査用ファイルへウェイクワードスコアを追記する。"""
        if self._score_log_path is None:
            return
        try:
            self._score_log_path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            line = f"{timestamp} score={score:.4f} threshold={threshold:.4f} detected={int(detected)}\n"
            with self._score_log_path.open("a", encoding="utf-8") as file:
                file.write(line)
        except OSError:
            log.debug("ウェイクワードスコアログを書き込めませんでした", exc_info=True)

    def _open_arecord(self) -> subprocess.Popen:
        """候補デバイスからarecord raw入力を開始する。"""
        last_error = ""
        for device in self._devices:
            cmd = [
                "arecord",
                "-D",
                device,
                "-f",
                "S16_LE",
                "-r",
                str(self._capture_sample_rate),
                "-c",
                "1",
                "-t",
                "raw",
                "-",
            ]
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                time.sleep(0.1)
                if proc.poll() is None:
                    log.info("ウェイクワード監視開始: %s", " ".join(cmd))
                    return proc
                last_error = proc.stderr.read().decode(errors="replace").strip() if proc.stderr else ""
            except OSError as exc:
                last_error = str(exc)
        raise RuntimeError(f"ウェイクワード入力デバイスを開けません: {last_error}")

    def _record_utterance(self, proc: subprocess.Popen, bytes_per_chunk: int, pre_roll_frames: list[bytes] | None = None) -> str:
        """検知後の発話を無音または最大秒数までWAVへ保存する。"""
        if self._endpoint_mode == "vad":
            vad_model = self._load_vad_model()
            if vad_model is not None:
                return self._record_utterance_vad(proc, bytes_per_chunk, vad_model, pre_roll_frames)
            log.warning("VADモデルを使えないためRMS終了判定へフォールバックします")
        return self._record_utterance_rms(proc, bytes_per_chunk, pre_roll_frames)

    def _record_utterance_rms(self, proc: subprocess.Popen, bytes_per_chunk: int, pre_roll_frames: list[bytes] | None = None) -> str:
        """検知後の発話をRMS無音判定または最大秒数までWAVへ保存する。"""
        frames: list[bytes] = list(pre_roll_frames or [])
        started_at = time.monotonic()
        silent_since: float | None = None
        while not self._stop.is_set():
            data = proc.stdout.read(bytes_per_chunk) if proc.stdout else b""
            if not data:
                break
            model_data = _resample_pcm16(data, self._capture_sample_rate, SAMPLE_RATE)
            frames.append(model_data)
            elapsed = time.monotonic() - started_at
            rms = _pcm16_rms(model_data)
            if elapsed >= self._record_min_seconds and rms < self._silence_rms_threshold:
                silent_since = silent_since or time.monotonic()
                if time.monotonic() - silent_since >= self._record_silence_seconds:
                    break
            else:
                silent_since = None
            if elapsed >= self._record_max_seconds:
                break
        if not frames:
            return ""
        wav_path = Path("/tmp/argos") / f"wakeword-{time.time_ns()}-{uuid.uuid4().hex}.wav"
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        _write_wav(wav_path, frames)
        return str(wav_path)

    def _record_utterance_vad(
        self,
        proc: subprocess.Popen,
        bytes_per_chunk: int,
        vad_model: SileroVadModel,
        pre_roll_frames: list[bytes] | None = None,
    ) -> str:
        """検知後の発話をVADの発話終了判定または最大秒数までWAVへ保存する。"""
        frames: list[bytes] = list(pre_roll_frames or [])
        samples = _pcm16_frames_to_float(frames)
        started_at = time.monotonic()
        last_check = 0.0
        check_interval = max(0.05, self._vad_check_seconds)
        import numpy as np

        from argos.services.wakeword.vad import estimate_endpoint_from_vad

        while not self._stop.is_set():
            data = proc.stdout.read(bytes_per_chunk) if proc.stdout else b""
            if not data:
                break
            model_data = _resample_pcm16(data, self._capture_sample_rate, SAMPLE_RATE)
            frames.append(model_data)
            chunk_samples = _pcm16_frames_to_float([model_data])
            samples = np.concatenate([samples, chunk_samples]) if samples.size else chunk_samples
            elapsed = time.monotonic() - started_at
            now = time.monotonic()
            if now - last_check >= check_interval:
                last_check = now
                probabilities = vad_model.predict(samples)
                end_seconds = estimate_endpoint_from_vad(
                    probabilities,
                    threshold=self._vad_threshold,
                    min_seconds=self._record_min_seconds,
                    min_silence_seconds=self._vad_min_silence_seconds,
                )
                if end_seconds is not None:
                    log.info("VAD発話終了: end=%.2fs elapsed=%.2fs", end_seconds, elapsed)
                    break
            if elapsed >= self._record_max_seconds:
                log.info("VAD発話録音が最大秒数に到達しました: %.2fs", elapsed)
                break
        if not frames:
            return ""
        wav_path = Path("/tmp/argos") / f"wakeword-{time.time_ns()}-{uuid.uuid4().hex}.wav"
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        _write_wav(wav_path, frames)
        return str(wav_path)

    def _load_vad_model(self) -> object | None:
        """Silero VADモデルを遅延読み込みする。"""
        if self._vad_model is not None:
            return self._vad_model
        from argos.services.wakeword.vad import SileroVadModel, find_default_vad_model

        model_path = Path(self._vad_model_path).expanduser() if self._vad_model_path else find_default_vad_model(self._model_dir)
        try:
            self._vad_model = SileroVadModel(model_path)
        except Exception:
            log.exception("Silero VADモデルを初期化できません: %s", model_path)
            return None
        return self._vad_model

    def _stop_process(self) -> None:
        """arecordプロセスを停止する。"""
        proc = self._proc
        self._proc = None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
            proc.wait(timeout=1)


def load_default_threshold(model_dir: str | Path, fallback: float) -> float:
    """設定値のしきい値を返す。

    学習時の評価JSONは検証用の参考値なので、常時監視では自動採用しない。
    """
    eval_path = Path(model_dir) / "argos_eval.json"
    if not eval_path.exists():
        return fallback
    try:
        json.loads(eval_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    return fallback


def _pcm16_samples(data: bytes) -> tuple[int, ...]:
    """PCM16 little-endianを整数サンプルへ変換する。"""
    sample_count = len(data) // 2
    if sample_count <= 0:
        return ()
    return struct.unpack(f"<{sample_count}h", data[: sample_count * 2])


def _resample_pcm16(data: bytes, from_rate: int, to_rate: int) -> bytes:
    """PCM16音声をモデル入力用のサンプルレートへ変換する。"""
    if from_rate == to_rate:
        return data
    import numpy as np

    samples = np.frombuffer(data[: len(data) - (len(data) % 2)], dtype="<i2")
    if samples.size == 0:
        return b""
    if from_rate > to_rate and from_rate % to_rate == 0:
        ratio = from_rate // to_rate
        usable = (samples.size // ratio) * ratio
        if usable <= 0:
            return b""
        downsampled = samples[:usable].reshape(-1, ratio).mean(axis=1)
        return np.clip(downsampled, -32768, 32767).astype("<i2").tobytes()
    duration = samples.size / float(from_rate)
    target_size = max(1, int(round(duration * to_rate)))
    source_index = np.linspace(0, samples.size - 1, num=samples.size, dtype=np.float32)
    target_index = np.linspace(0, samples.size - 1, num=target_size, dtype=np.float32)
    converted = np.interp(target_index, source_index, samples.astype(np.float32))
    return np.clip(converted, -32768, 32767).astype("<i2").tobytes()


def _float_waveform(samples: Iterable[int]):
    """整数サンプル列をfloat32波形へ変換する。"""
    import numpy as np

    return np.asarray(tuple(samples), dtype=np.float32) / 32768.0


def _pcm16_frames_to_float(frames: list[bytes]) -> np.ndarray:
    """PCM16フレーム列を16kHz float32波形へ変換する。"""
    import numpy as np

    if not frames:
        return np.array([], dtype=np.float32)
    audio = np.frombuffer(b"".join(frames), dtype=np.int16)
    if audio.size == 0:
        return np.array([], dtype=np.float32)
    return audio.astype(np.float32) / 32768.0


def _pcm16_rms(data: bytes) -> float:
    """PCM16 chunkのRMS音量を返す。"""
    samples = _pcm16_samples(data)
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def _write_wav(path: Path, frames: list[bytes]) -> None:
    """PCM16 mono 16kHzフレームをWAVファイルへ書き込む。"""
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(b"".join(frames))


def ensure_wakeword_library_path() -> None:
    """ONNX Runtime共有ライブラリの探索パスを必要なら補う。"""
    if os.getenv("ARGOS_WAKEWORD_LD_READY") == "1":
        return
    try:
        import onnxruntime
    except Exception:
        return
    capi_dir = str(Path(onnxruntime.__file__).resolve().parent / "capi")
    current = os.environ.get("LD_LIBRARY_PATH", "")
    if capi_dir in current.split(":"):
        return
    os.environ["LD_LIBRARY_PATH"] = f"{capi_dir}:{current}" if current else capi_dir
