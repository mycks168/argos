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
from collections.abc import Iterable


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

    def __init__(self, device: str | Iterable[str], sample_rate: int) -> None:
        """録音デバイス候補とサンプリングレートを保持する。"""
        self._devices = (device,) if isinstance(device, str) else tuple(device)
        if not self._devices:
            raise ValueError("録音デバイス候補が空です")
        self._device = self._devices[0]
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
        self._device = select_available_input_device((self._device, *self._devices))
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
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=1)
        finally:
            self._proc = None

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


def select_available_input_device(devices: Iterable[str]) -> str:
    """候補から現在接続されているALSA入力デバイスを選ぶ。"""
    candidates = tuple(device for device in devices if device)
    if not candidates:
        raise ValueError("録音デバイス候補が空です")
    cards = _read_asound_cards()
    if not cards:
        return candidates[0]
    for device in candidates:
        card = _extract_card_name(device)
        if not card or _card_exists(cards, card):
            return device
    auto_devices = _list_capture_devices()
    if auto_devices:
        log.warning("録音デバイス候補が見つかりません。検出した入力デバイスを使います: %s", auto_devices[0])
        return auto_devices[0]
    log.warning("録音デバイス候補が見つかりません。先頭候補を使います: %s", candidates[0])
    return candidates[0]


def _read_asound_cards() -> str:
    """ALSAカード一覧を読み込む。"""
    try:
        return Path("/proc/asound/cards").read_text(encoding="utf-8")
    except OSError:
        return ""


def _extract_card_name(device: str) -> str:
    """ALSAデバイス文字列からCARD名を取り出す。"""
    marker = "CARD="
    if marker not in device:
        return ""
    rest = device.split(marker, 1)[1]
    return rest.split(",", 1)[0].strip()


def _card_exists(cards_text: str, card: str) -> bool:
    """ALSAカード一覧に指定カードが存在するか返す。"""
    if card.isdigit():
        return any(line.lstrip().startswith(f"{card} ") for line in cards_text.splitlines())
    return card in _asound_card_names(cards_text)


def _asound_card_names(cards_text: str) -> set[str]:
    """ALSAカード一覧から空白を除いたカード名を取り出す。"""
    names = set()
    for line in cards_text.splitlines():
        if "[" not in line or "]" not in line:
            continue
        name = line.split("[", 1)[1].split("]", 1)[0].strip()
        if name:
            names.add(name)
    return names


def _list_capture_devices() -> tuple[str, ...]:
    """arecord -l から録音可能なplughwデバイス候補を作る。"""
    try:
        result = subprocess.run(["arecord", "-l"], text=True, capture_output=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if result.returncode != 0:
        return ()
    devices = []
    for line in result.stdout.splitlines():
        parsed = _parse_arecord_capture_line(line)
        if parsed:
            devices.append(parsed)
    return tuple(devices)


def _parse_arecord_capture_line(line: str) -> str:
    """arecord -l のcard/device行からplughwデバイス文字列を作る。"""
    import re

    match = re.search(r"card\s+\d+:\s+([^\s\[]+).*device\s+(\d+):", line)
    if not match:
        return ""
    card, device = match.groups()
    return f"plughw:CARD={card},DEV={device}"


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
