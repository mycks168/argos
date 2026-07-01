"""ALSA を使った録音と音声再生。"""

from __future__ import annotations

import io
import math
import os
import signal
import struct
import subprocess
import threading
import time
import uuid
import wave
import logging
from pathlib import Path
from collections.abc import Iterable
import queue


WAV_PATH = "/tmp/argos/utterance.wav"
WAV_PREFIX = "utterance-"
STALE_RECORDING_SECONDS = 60 * 60
STT_LEADING_SILENCE_SECONDS = 0.2
STT_TRAILING_SILENCE_SECONDS = 0.5
log = logging.getLogger(__name__)


class AudioInputSubscription:
    """共有マイク入力から音声チャンクを受け取る購読ハンドル。"""

    def __init__(self, source: "AudioInputStream", subscriber_id: int, frames: "queue.Queue[bytes]") -> None:
        """購読元とキューを保持する。"""
        self._source = source
        self._subscriber_id = subscriber_id
        self._frames = frames
        self._closed = False

    def read(self, timeout: float = 1.0) -> bytes:
        """次のPCM16 monoチャンクを返す。タイムアウト時は空bytesを返す。"""
        if self._closed:
            return b""
        try:
            return self._frames.get(timeout=timeout)
        except queue.Empty:
            return b""

    def close(self) -> None:
        """購読を解除する。"""
        if self._closed:
            return
        self._closed = True
        self._source.unsubscribe(self._subscriber_id)


class AudioInputStream:
    """1つのarecord入力を複数処理へ配信する共有マイクストリーム。"""

    def __init__(
        self,
        devices: str | Iterable[str],
        capture_sample_rate: int,
        output_sample_rate: int,
        *,
        chunk_ms: int = 80,
        subscriber_queue_chunks: int = 256,
    ) -> None:
        """入力デバイス候補と配信サンプルレートを保持する。"""
        self._devices = (devices,) if isinstance(devices, str) else tuple(devices)
        if not self._devices:
            raise ValueError("共有マイク入力デバイス候補が空です")
        self._device = self._devices[0]
        self._capture_sample_rate = max(1, int(capture_sample_rate))
        self._output_sample_rate = max(1, int(output_sample_rate))
        self._chunk_ms = max(10, int(chunk_ms))
        self._subscriber_queue_chunks = max(1, int(subscriber_queue_chunks))
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._subscribers: dict[int, queue.Queue[bytes]] = {}
        self._next_subscriber_id = 1
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None

    @property
    def output_sample_rate(self) -> int:
        """購読者へ配信するサンプルレートを返す。"""
        return self._output_sample_rate

    def start(self) -> None:
        """共有マイク入力を開始する。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """共有マイク入力を停止する。"""
        self._stop.set()
        self._stop_process()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
        with self._lock:
            self._subscribers.clear()

    def subscribe(self) -> AudioInputSubscription:
        """音声チャンク購読を作成する。"""
        self.start()
        frames: queue.Queue[bytes] = queue.Queue(maxsize=self._subscriber_queue_chunks)
        with self._lock:
            subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            self._subscribers[subscriber_id] = frames
        return AudioInputSubscription(self, subscriber_id, frames)

    def unsubscribe(self, subscriber_id: int) -> None:
        """指定購読者を解除する。"""
        with self._lock:
            self._subscribers.pop(subscriber_id, None)

    def _run(self) -> None:
        """arecord raw入力を読み、購読者へ配信し続ける。"""
        while not self._stop.is_set():
            try:
                self._run_process()
            except Exception:
                log.exception("共有マイク入力が失敗しました。再試行します")
                if self._stop.wait(2.0):
                    return

    def _run_process(self) -> None:
        """arecordプロセスを開いて音声を配信する。"""
        proc = self._open_arecord()
        self._proc = proc
        chunk_size = max(1, int(self._capture_sample_rate * self._chunk_ms / 1000))
        bytes_per_chunk = chunk_size * 2
        try:
            while not self._stop.is_set():
                data = proc.stdout.read(bytes_per_chunk) if proc.stdout else b""
                if not data:
                    raise RuntimeError("共有マイク入力の音声ストリームが終了しました")
                model_data = _resample_pcm16(data, self._capture_sample_rate, self._output_sample_rate)
                self._publish(model_data)
        finally:
            self._stop_process()

    def _open_arecord(self) -> subprocess.Popen:
        """候補デバイスからarecord raw入力を開始する。"""
        self._device = select_available_input_device((self._device, *self._devices))
        cmd = [
            "arecord",
            "-D",
            self._device,
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
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(0.1)
        if proc.poll() is None:
            log.info("共有マイク入力開始: %s", " ".join(cmd))
            return proc
        stderr = proc.stderr.read().decode(errors="replace").strip() if proc.stderr else ""
        raise RuntimeError(f"共有マイク入力デバイスを開けません: {stderr}")

    def _publish(self, data: bytes) -> None:
        """購読者キューへ音声を配信する。"""
        with self._lock:
            subscribers = tuple(self._subscribers.values())
        for frames in subscribers:
            if frames.full():
                try:
                    frames.get_nowait()
                except queue.Empty:
                    pass
            try:
                frames.put_nowait(data)
            except queue.Full:
                pass

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


class StreamRecorder:
    """共有マイクストリームからPTT録音用WAVを切り出す。"""

    def __init__(self, source: AudioInputStream, sample_rate: int) -> None:
        """共有入力とWAVサンプルレートを保持する。"""
        self._source = source
        self._sample_rate = sample_rate
        self._wav_path = WAV_PATH
        self._subscription: AudioInputSubscription | None = None
        self._frames: list[bytes] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._recording = False
        self._lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        """共有ストリームから録音中か返す。"""
        with self._lock:
            return self._recording

    def start(self) -> None:
        """共有ストリームから録音を開始する。"""
        with self._lock:
            if self._recording:
                log.warning("録音開始をスキップしました: 既に共有ストリーム録音中です")
                return
            recording_dir = Path(WAV_PATH).parent
            recording_dir.mkdir(parents=True, exist_ok=True)
            self._wav_path = str(recording_dir / f"{WAV_PREFIX}{time.time_ns()}-{uuid.uuid4().hex}.wav")
            self._frames = []
            self._stop.clear()
            self._subscription = self._source.subscribe()
            self._recording = True
            self._thread = threading.Thread(target=self._record_loop, daemon=True)
            self._thread.start()
        log.info("共有ストリーム録音開始: %s", self._wav_path)

    def stop(self) -> str:
        """録音を停止し、WAVパスを返す。"""
        with self._lock:
            if not self._recording:
                return self._wav_path
            self._recording = False
            thread = self._thread
            subscription = self._subscription
        self._stop.set()
        if thread is not None:
            thread.join(timeout=3)
        if subscription is not None:
            subscription.close()
        frames = self._frames
        if not frames:
            raise RuntimeError("共有ストリーム録音データが空です")
        _write_pcm16_wav(Path(self._wav_path), frames, self._sample_rate)
        _pad_wav_silence(self._wav_path, STT_LEADING_SILENCE_SECONDS, STT_TRAILING_SILENCE_SECONDS)
        with self._lock:
            self._subscription = None
            self._thread = None
        return self._wav_path

    def cancel(self) -> None:
        """録音を破棄して停止する。"""
        with self._lock:
            was_recording = self._recording
            self._recording = False
            thread = self._thread
            subscription = self._subscription
        if not was_recording:
            log.info("録音キャンセル: 共有ストリーム録音はありません")
            return
        self._stop.set()
        if thread is not None:
            thread.join(timeout=2)
        if subscription is not None:
            subscription.close()
        with self._lock:
            self._subscription = None
            self._thread = None
            self._frames = []
        _remove_file_quietly(self._wav_path)

    def _record_loop(self) -> None:
        """購読した音声チャンクをWAV用フレームへ蓄積する。"""
        subscription = self._subscription
        if subscription is None:
            return
        while not self._stop.is_set():
            data = subscription.read(timeout=0.2)
            if data:
                self._frames.append(data)


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
        self._wav_path = WAV_PATH

    @property
    def is_recording(self) -> bool:
        """録音プロセスが生きているか返す。"""
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        """録音を開始する。"""
        if self.is_recording:
            log.warning("録音開始をスキップしました: 既に arecord が動作中です")
            return
        recording_dir = Path(WAV_PATH).parent
        recording_dir.mkdir(parents=True, exist_ok=True)
        self._wav_path = str(recording_dir / f"{WAV_PREFIX}{time.time_ns()}-{uuid.uuid4().hex}.wav")
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
            self._wav_path,
        ]
        log.info("録音開始: %s", " ".join(cmd))
        self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def stop(self) -> str:
        """録音を停止し、WAV パスを返す。"""
        proc = self._proc
        if proc is None:
            return self._wav_path
        wav_path = self._wav_path
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
        if not os.path.exists(wav_path):
            raise RuntimeError(f"録音ファイルが作成されませんでした。device={self._device}, stderr={stderr}")
        file_size = os.path.getsize(wav_path)
        if file_size < 100:
            raise RuntimeError(f"録音ファイルが小さすぎます。size={file_size}, device={self._device}, stderr={stderr}")
        if proc.returncode not in (0, -signal.SIGINT):
            if _is_expected_arecord_interrupt(stderr):
                log.debug("arecord は SIGINT 停止時に code=%s を返しましたが、WAV は作成済みです", proc.returncode)
            else:
                raise RuntimeError(f"arecord が失敗しました。device={self._device}, code={proc.returncode}, stderr={stderr}")
        _repair_wav_header(wav_path)
        _pad_wav_silence(wav_path, STT_LEADING_SILENCE_SECONDS, STT_TRAILING_SILENCE_SECONDS)
        return wav_path

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
        _remove_file_quietly(self._wav_path)


def cleanup_stale_recordings(max_age_seconds: int = STALE_RECORDING_SECONDS) -> int:
    """古い録音一時ファイルを削除し、削除数を返す。"""
    recording_dir = Path(WAV_PATH).parent
    if not recording_dir.exists():
        return 0
    threshold = time.time() - max_age_seconds
    removed = 0
    for path in recording_dir.glob(f"{WAV_PREFIX}*.wav"):
        try:
            if path.stat().st_mtime < threshold:
                path.unlink()
                removed += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            log.warning("古い録音ファイルを削除できませんでした: %s: %s", path, exc)
    return removed


def _remove_file_quietly(path: str) -> None:
    """不要になった録音ファイルを存在する場合だけ削除する。"""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("録音ファイルを削除できませんでした: %s: %s", path, exc)


def _pad_wav_silence(wav_path: str, leading_seconds: float, trailing_seconds: float) -> None:
    """短い発話でもSTTが扱いやすいよう、WAVの前後へ無音を追加する。"""
    try:
        with wave.open(wav_path, "rb") as wav_file:
            params = wav_file.getparams()
            frames = wav_file.readframes(wav_file.getnframes())
        if params.comptype != "NONE":
            return
        frame_size = params.nchannels * params.sampwidth
        leading_frames = max(0, int(params.framerate * leading_seconds))
        trailing_frames = max(0, int(params.framerate * trailing_seconds))
        silence = b"\x00" * frame_size
        padded = silence * leading_frames + frames + silence * trailing_frames
        with wave.open(wav_path, "wb") as wav_file:
            wav_file.setparams(params)
            wav_file.writeframes(padded)
        log.debug("STT向けにWAVへ無音を追加しました: %s leading=%.2f trailing=%.2f", wav_path, leading_seconds, trailing_seconds)
    except (wave.Error, OSError) as exc:
        log.debug("WAV無音追加をスキップしました: %s: %s", wav_path, exc)


class AudioPlayer:
    """aplay に WAV バイト列を渡して再生する。"""

    def __init__(self, output_device: str, output_card: str, volume: int) -> None:
        """出力先デバイスと音量設定を保持する。"""
        self._output_device = output_device
        self._output_card = output_card
        self._volume = volume
        self._volume_lock = threading.Lock()
        self._volume_set = False
        self._proc: subprocess.Popen | None = None

    @property
    def volume(self) -> int:
        """現在の再生音量を返す。"""
        with self._volume_lock:
            return self._volume

    @property
    def is_playing(self) -> bool:
        """現在再生プロセスが動いている場合はTrueを返す。"""
        proc = self._proc
        return proc is not None and proc.poll() is None

    def set_volume(self, volume: int) -> int:
        """再生音量を0から100の範囲に丸めて反映し、反映後の値を返す。"""
        with self._volume_lock:
            self._volume = max(0, min(100, int(volume)))
            applied = self._volume
        self._apply_volume()
        self._volume_set = True
        return applied

    def play_wav(self, wav_data: bytes) -> None:
        """WAV データを同期的に再生する。"""
        self._set_volume_once()
        if self._play_wav_streaming(wav_data):
            return
        wav_data = self._scale_wav_volume(wav_data)
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
        if self._volume_set:
            return
        self._apply_volume()
        self._volume_set = True

    def _apply_volume(self) -> None:
        """amixer で現在の音量を出力カードへ反映する。"""
        volume = f"{self.volume}%"
        device_args = ["-D", f"hw:CARD={self._output_card}"] if self._output_card else []
        for control in ("Master", "PCM", "Headphone", "Speaker"):
            subprocess.run(
                ["amixer", "-q", *device_args, "set", control, volume],
                capture_output=True,
                check=False,
            )

    def _play_wav_streaming(self, wav_data: bytes) -> bool:
        """16bit PCM WAVを小分けにして、再生中の音量変更を反映する。"""
        try:
            source = wave.open(io.BytesIO(wav_data), "rb")
        except (wave.Error, EOFError):
            return False
        with source:
            params = source.getparams()
            if params.sampwidth != 2:
                return False
            proc = subprocess.Popen(
                [
                    "aplay",
                    "-q",
                    "-D",
                    self._output_device,
                    "-t",
                    "raw",
                    "-f",
                    "S16_LE",
                    "-r",
                    str(params.framerate),
                    "-c",
                    str(params.nchannels),
                    "-",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._proc = proc
            try:
                if proc.stdin is None:
                    return False
                previous_volume = self.volume
                while True:
                    frames = source.readframes(2048)
                    if not frames:
                        break
                    current_volume = self.volume
                    proc.stdin.write(self._scale_pcm16_ramp(frames, previous_volume, current_volume, params.nchannels))
                    previous_volume = current_volume
                proc.stdin.close()
                proc.wait(timeout=120)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                proc.kill()
                proc.wait(timeout=1)
            finally:
                self._proc = None
            return True

    def _scale_wav_volume(self, wav_data: bytes) -> bytes:
        """16bit PCM WAVのサンプルへソフトウェア音量を反映する。"""
        volume = self.volume
        if volume == 100:
            return wav_data
        try:
            with wave.open(io.BytesIO(wav_data), "rb") as source:
                params = source.getparams()
                if params.sampwidth != 2:
                    return wav_data
                frames = source.readframes(params.nframes)
        except (wave.Error, EOFError):
            return wav_data
        scaled = self._scale_pcm16(frames, volume)
        with io.BytesIO() as buffer:
            with wave.open(buffer, "wb") as target:
                target.setparams(params)
                target.writeframes(scaled)
            return buffer.getvalue()

    def _scale_pcm16(self, frames: bytes, volume: int) -> bytes:
        """16bit little-endian PCMフレームへ指定音量を掛ける。"""
        volume = max(0, min(100, int(volume)))
        if volume == 100:
            return frames
        factor = volume / 100
        sample_count = len(frames) // 2
        samples = struct.unpack(f"<{sample_count}h", frames[: sample_count * 2])
        scaled = bytearray()
        for sample in samples:
            value = max(-32768, min(32767, int(sample * factor)))
            scaled.extend(struct.pack("<h", value))
        scaled.extend(frames[sample_count * 2 :])
        return bytes(scaled)

    def _scale_pcm16_ramp(self, frames: bytes, start_volume: int, end_volume: int, channels: int) -> bytes:
        """16bit PCMフレームへ音量を滑らかに掛ける。"""
        start_volume = max(0, min(100, int(start_volume)))
        end_volume = max(0, min(100, int(end_volume)))
        if start_volume == end_volume:
            return self._scale_pcm16(frames, end_volume)
        sample_count = len(frames) // 2
        frame_count = max(1, sample_count // max(1, channels))
        samples = struct.unpack(f"<{sample_count}h", frames[: sample_count * 2])
        scaled = bytearray()
        for index, sample in enumerate(samples):
            frame_index = min(frame_count - 1, index // max(1, channels))
            progress = frame_index / max(1, frame_count - 1)
            volume = start_volume + (end_volume - start_volume) * progress
            value = max(-32768, min(32767, int(sample * volume / 100)))
            scaled.extend(struct.pack("<h", value))
        scaled.extend(frames[sample_count * 2 :])
        return bytes(scaled)


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


def _resample_pcm16(data: bytes, from_rate: int, to_rate: int) -> bytes:
    """PCM16音声を指定サンプルレートへ変換する。"""
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


def _write_pcm16_wav(path: Path, frames: list[bytes], sample_rate: int) -> None:
    """PCM16 monoフレームをWAVファイルへ書き込む。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"".join(frames))
