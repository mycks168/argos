"""Silero VADを使ったウェイクワード後発話の終了判定。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


SAMPLE_RATE = 16000
WINDOW_SAMPLES = 512
CONTEXT_SAMPLES = 64


@dataclass(frozen=True)
class VadSegment:
    """VADが検出した発話区間。"""

    start_seconds: float
    end_seconds: float


class SileroVadModel:  # pragma: no cover
    """Silero VAD ONNXモデルを実行する。"""

    def __init__(self, path: str | Path) -> None:
        """ONNX Runtimeセッションを初期化する。"""
        import onnxruntime

        model_path = Path(path).expanduser()
        if not model_path.exists():
            raise FileNotFoundError(f"Silero VADモデルが見つかりません: {model_path}")
        options = onnxruntime.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        options.enable_cpu_mem_arena = False
        options.log_severity_level = 4
        self._session = onnxruntime.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
            sess_options=options,
        )

    def predict(self, samples: np.ndarray) -> np.ndarray:
        """16kHz mono float32音声からチャンクごとの発話確率を返す。"""
        audio = np.asarray(samples, dtype=np.float32).flatten()
        if audio.size == 0:
            return np.array([], dtype=np.float32)
        padding = (-audio.size) % WINDOW_SAMPLES
        if padding:
            audio = np.pad(audio, (0, padding))
        chunks = audio.reshape(-1, WINDOW_SAMPLES)
        context = chunks[..., -CONTEXT_SAMPLES:]
        context[-1] = 0
        context = np.roll(context, 1, 0)
        model_input = np.concatenate([context, chunks], axis=1).astype(np.float32)
        h = np.zeros((1, 1, 128), dtype=np.float32)
        c = np.zeros((1, 1, 128), dtype=np.float32)
        outputs: list[np.ndarray] = []
        for start in range(0, model_input.shape[0], 10000):
            output, h, c = self._session.run(
                None,
                {"input": model_input[start : start + 10000], "h": h, "c": c},
            )
            outputs.append(output.reshape(-1))
        return np.concatenate(outputs).astype(np.float32)


def find_default_vad_model(model_dir: str | Path = "models/wakeword") -> Path:
    """ローカルで見つかる既定のSilero VADモデルパスを返す。"""
    model_dir_path = Path(model_dir).expanduser()
    candidates = [
        model_dir_path / "silero_vad_v6.onnx",
        model_dir_path / "silero_vad.onnx",
        Path("models/silero_vad_v6.onnx"),
        Path.home() / ".cache/uv/archive-v0/REEgxVCUAZ8vX6qYMgGyU/faster_whisper/assets/silero_vad_v6.onnx",
        Path.home() / "argos/.venv/lib/python3.11/site-packages/openwakeword/resources/models/silero_vad.onnx",
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def detect_vad_segments(
    probabilities: np.ndarray,
    *,
    threshold: float,
    min_speech_seconds: float,
    min_silence_seconds: float,
) -> list[VadSegment]:
    """VAD確率列から発話区間を抽出する。"""
    segments: list[VadSegment] = []
    in_speech = False
    start_index = 0
    last_speech_index = 0
    min_speech_chunks = max(1, int(min_speech_seconds * SAMPLE_RATE / WINDOW_SAMPLES))
    min_silence_chunks = max(1, int(min_silence_seconds * SAMPLE_RATE / WINDOW_SAMPLES))
    for index, probability in enumerate(probabilities):
        if probability >= threshold:
            if not in_speech:
                start_index = index
                in_speech = True
            last_speech_index = index
            continue
        if in_speech and index - last_speech_index >= min_silence_chunks:
            if last_speech_index - start_index + 1 >= min_speech_chunks:
                segments.append(
                    VadSegment(
                        start_seconds=start_index * WINDOW_SAMPLES / SAMPLE_RATE,
                        end_seconds=(last_speech_index + 1) * WINDOW_SAMPLES / SAMPLE_RATE,
                    )
                )
            in_speech = False
    if in_speech and probabilities.size - start_index >= min_speech_chunks:
        segments.append(
            VadSegment(
                start_seconds=start_index * WINDOW_SAMPLES / SAMPLE_RATE,
                end_seconds=probabilities.size * WINDOW_SAMPLES / SAMPLE_RATE,
            )
        )
    return segments


def estimate_endpoint_from_vad(
    probabilities: np.ndarray,
    *,
    threshold: float,
    min_seconds: float,
    min_silence_seconds: float,
) -> float | None:
    """VAD確率列から発話終了候補秒を返す。"""
    min_chunks = max(1, int(min_seconds * SAMPLE_RATE / WINDOW_SAMPLES))
    min_silence_chunks = max(1, int(min_silence_seconds * SAMPLE_RATE / WINDOW_SAMPLES))
    speech_seen = False
    last_speech_index = 0
    for index, probability in enumerate(probabilities):
        if probability >= threshold:
            speech_seen = True
            last_speech_index = index
            continue
        if speech_seen and index >= min_chunks and index - last_speech_index >= min_silence_chunks:
            return (last_speech_index + 1) * WINDOW_SAMPLES / SAMPLE_RATE
    return None
