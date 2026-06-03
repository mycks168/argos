"""ALSA を使った録音と音声再生。"""

from __future__ import annotations

import math
import os
import signal
import struct
import subprocess
import wave
import logging
from pathlib import Path


WAV_PATH = "/tmp/argos/utterance.wav"
log = logging.getLogger(__name__)


def check_audio_level(wav_path: str) -> float:
    """16bit PCM WAV の RMS 音量を返す。"""
    try:
        with wave.open(wav_path, "rb") as wav_file:
            raw = wav_file.readframes(wav_file.getnframes())
    except (wave.Error, OSError):
        return 0.0
    sample_count = len(raw) // 2
    if sample_count == 0:
        return 0.0
    samples = struct.unpack(f"<{sample_count}h", raw[: sample_count * 2])
    return math.sqrt(sum(sample * sample for sample in samples) / sample_count)


class Recorder:
    """arecord プロセスを制御して PTT 録音を行う。"""

    def __init__(self, device: str, sample_rate: int) -> None:
        """録音デバイスとサンプリングレートを保持する。"""
        self._device = device
        self._sample_rate = sample_rate
        self._proc: subprocess.Popen | None = None

    @property
    def is_recording(self) -> bool:
        """録音プロセスが生きているか返す。"""
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        """録音を開始する。"""
        if self.is_recording:
            log.warning("録音開始をスキップしました: 既に arecord が動作中です")
            return
        Path(WAV_PATH).parent.mkdir(parents=True, exist_ok=True)
        try:
            os.remove(WAV_PATH)
        except FileNotFoundError:
            pass
        cmd = [
            "arecord",
            "-D",
            self._device,
            "-f",
            "S16_LE",
            "-r",
            str(self._sample_rate),
            "-c",
            "1",
            "-t",
            "wav",
            WAV_PATH,
        ]
        log.info("録音開始: %s", " ".join(cmd))
        self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def stop(self) -> str:
        """録音を停止し、WAV パスを返す。"""
        proc = self._proc
        if proc is None:
            return WAV_PATH
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
            proc.wait(timeout=2)
        finally:
            self._proc = None
        stderr = ""
        if proc.stderr:
            stderr = proc.stderr.read().decode(errors="replace").strip()
        if not os.path.exists(WAV_PATH):
            raise RuntimeError(f"録音ファイルが作成されませんでした。device={self._device}, stderr={stderr}")
        file_size = os.path.getsize(WAV_PATH)
        if file_size < 100:
            raise RuntimeError(f"録音ファイルが小さすぎます。size={file_size}, device={self._device}, stderr={stderr}")
        if proc.returncode not in (0, -signal.SIGINT):
            if _is_expected_arecord_interrupt(stderr):
                log.debug("arecord は SIGINT 停止時に code=%s を返しましたが、WAV は作成済みです", proc.returncode)
            else:
                raise RuntimeError(f"arecord が失敗しました。device={self._device}, code={proc.returncode}, stderr={stderr}")
        _repair_wav_header(WAV_PATH)
        return WAV_PATH

    def cancel(self) -> None:
        """録音を破棄して停止する。"""
        proc = self._proc
        if proc is None:
            log.info("録音キャンセル: 動作中の arecord はありません")
            return
        log.info("録音キャンセル: arecord を停止します")
        try:
            proc.kill()
            proc.wait(timeout=2)
        except OSError:
            pass
        self._proc = None


class AudioPlayer:
    """aplay に WAV バイト列を渡して再生する。"""

    def __init__(self, output_device: str, output_card: str, volume: int) -> None:
        """出力先デバイスと音量設定を保持する。"""
        self._output_device = output_device
        self._output_card = output_card
        self._volume = volume
        self._volume_set = False
        self._proc: subprocess.Popen | None = None

    def play_wav(self, wav_data: bytes) -> None:
        """WAV データを同期的に再生する。"""
        self._set_volume_once()
        proc = subprocess.Popen(
            ["aplay", "-q", "-D", self._output_device, "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._proc = proc
        try:
            proc.communicate(input=wav_data, timeout=120)
        finally:
            self._proc = None

    def cancel(self) -> None:
        """再生中の aplay を停止する。"""
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()

    def _set_volume_once(self) -> None:
        """amixer で音量を一度だけ設定する。"""
        if self._volume_set or not self._output_card:
            return
        volume = f"{self._volume}%"
        for control in ("Master", "PCM", "Headphone", "Speaker"):
            subprocess.run(
                ["amixer", "-q", "-D", f"hw:CARD={self._output_card}", "set", control, volume],
                capture_output=True,
                check=False,
            )
        self._volume_set = True


def _is_expected_arecord_interrupt(stderr: str) -> bool:
    """arecord を SIGINT 停止した時の既知 stderr か判定する。"""
    return "Aborted by signal Interrupt" in stderr or "Interrupted system call" in stderr


def _repair_wav_header(wav_path: str) -> None:
    """arecord停止時に残る不正なWAVフレーム数を実データ量に合わせて修復する。"""
    try:
        with wave.open(wav_path, "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            frame_rate = wav_file.getframerate()
            declared_frames = wav_file.getnframes()
            raw = wav_file.readframes(declared_frames)
    except (wave.Error, OSError):
        return
    bytes_per_frame = channels * sample_width
    if bytes_per_frame <= 0:
        return
    actual_frames = len(raw) // bytes_per_frame
    if actual_frames == declared_frames:
        return
    tmp_path = f"{wav_path}.repair"
    with wave.open(tmp_path, "wb") as repaired:
        repaired.setnchannels(channels)
        repaired.setsampwidth(sample_width)
        repaired.setframerate(frame_rate)
        repaired.writeframes(raw[: actual_frames * bytes_per_frame])
    os.replace(tmp_path, wav_path)
    log.info("WAVヘッダを修復しました: declared=%s actual=%s", declared_frames, actual_frames)
