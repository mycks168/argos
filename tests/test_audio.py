import os
import wave
from io import BytesIO
from pathlib import Path

from argos.hardware.audio import (
    AudioPlayer,
    Recorder,
    _pad_wav_silence,
    _repair_wav_header,
    cleanup_stale_recordings,
    select_available_input_device,
)


class FakeProc:
    def __init__(self):
        self.signals = []
        self.killed = False
        self.terminated = False
        self.stdin = self
        self.stderr = None
        self.returncode = 0
        self.wait_calls = 0
        self.input = b""

    def poll(self):
        return None

    def send_signal(self, signal):
        self.signals.append(signal)

    def wait(self, timeout=None):
        self.wait_calls += 1
        return 0

    def kill(self):
        self.killed = True

    def terminate(self):
        self.terminated = True

    def communicate(self, input=None, timeout=None):
        self.input = input
        return b"", b""

    def write(self, data):
        self.input += data
        return len(data)

    def flush(self):
        pass

    def close(self):
        pass


def _recording_path(command):
    """arecordコマンドから録音先WAVパスを取り出す。"""
    return command[-1]


def test_recorder_start_stop_cancel(monkeypatch, tmp_path):
    proc = FakeProc()
    calls = []
    wav_path = tmp_path / "u.wav"
    monkeypatch.setattr("argos.hardware.audio.WAV_PATH", str(wav_path))
    monkeypatch.setattr("argos.hardware.audio.subprocess.Popen", lambda command, **kwargs: calls.append(command) or proc)

    recorder = Recorder("mic", 16000)
    recorder.start()
    assert recorder.is_recording
    assert calls[0][0] == "arecord"
    assert "-D" in calls[0]
    first_path = _recording_path(calls[0])
    assert first_path != str(wav_path)
    assert first_path.startswith(str(tmp_path))
    Path(first_path).write_bytes(b"RIFF" + b"0" * 200)
    assert recorder.stop() == first_path

    recorder.start()
    second_path = _recording_path(calls[1])
    Path(second_path).write_bytes(b"RIFF" + b"1" * 200)
    recorder.cancel()
    assert proc.killed
    assert not Path(second_path).exists()


def test_recorder_selects_available_device_on_start(monkeypatch, tmp_path):
    """録音開始時に接続済みカードの候補を選ぶ。"""
    proc = FakeProc()
    calls = []
    wav_path = tmp_path / "u.wav"
    monkeypatch.setattr("argos.hardware.audio.WAV_PATH", str(wav_path))
    monkeypatch.setattr("argos.hardware.audio._read_asound_cards", lambda: " 1 [H2]: USB-Audio - HyperX\n")
    monkeypatch.setattr("argos.hardware.audio.subprocess.Popen", lambda command, **kwargs: calls.append(command) or proc)
    recorder = Recorder(("plughw:CARD=Missing,DEV=0", "plughw:CARD=H2,DEV=0"), 16000)

    recorder.start()

    assert calls[0][calls[0].index("-D") + 1] == "plughw:CARD=H2,DEV=0"


def test_recorder_reuses_detected_fallback_device(monkeypatch, tmp_path):
    """一度検出したフォールバック先は次回録音で優先する。"""
    proc = FakeProc()
    calls = []
    scan_count = 0
    wav_path = tmp_path / "u.wav"

    def list_capture_devices():
        nonlocal scan_count
        scan_count += 1
        return ("plughw:CARD=Microphone,DEV=0",)

    monkeypatch.setattr("argos.hardware.audio.WAV_PATH", str(wav_path))
    monkeypatch.setattr("argos.hardware.audio._read_asound_cards", lambda: " 2 [Microphone]: USB-Audio - USB Microphone\n")
    monkeypatch.setattr("argos.hardware.audio._list_capture_devices", list_capture_devices)
    monkeypatch.setattr("argos.hardware.audio.subprocess.Popen", lambda command, **kwargs: calls.append(command) or proc)
    recorder = Recorder(("plughw:CARD=Missing,DEV=0",), 16000)

    recorder.start()
    recorder.cancel()
    recorder.start()

    assert calls[0][calls[0].index("-D") + 1] == "plughw:CARD=Microphone,DEV=0"
    assert calls[1][calls[1].index("-D") + 1] == "plughw:CARD=Microphone,DEV=0"
    assert scan_count == 1


def test_select_available_input_device_falls_back_to_first(monkeypatch):
    """候補が見つからない場合は先頭候補を返す。"""
    monkeypatch.setattr("argos.hardware.audio._read_asound_cards", lambda: " 1 [H2]: USB-Audio - HyperX\n")
    monkeypatch.setattr("argos.hardware.audio._list_capture_devices", lambda: ())

    assert select_available_input_device(("plughw:CARD=Missing,DEV=0", "plughw:CARD=Other,DEV=0")) == "plughw:CARD=Missing,DEV=0"


def test_select_available_input_device_uses_detected_capture_device(monkeypatch):
    """設定候補が外れている場合は検出した録音デバイスへフォールバックする。"""
    monkeypatch.setattr("argos.hardware.audio._read_asound_cards", lambda: " 2 [Microphone]: USB-Audio - USB Microphone\n")
    monkeypatch.setattr("argos.hardware.audio._list_capture_devices", lambda: ("plughw:CARD=Microphone,DEV=0",))

    assert select_available_input_device(("plughw:CARD=Missing,DEV=0",)) == "plughw:CARD=Microphone,DEV=0"


def test_select_available_input_device_matches_padded_card_name(monkeypatch):
    """ALSAカード名の右側空白を無視して候補を選ぶ。"""
    monkeypatch.setattr(
        "argos.hardware.audio._read_asound_cards",
        lambda: " 2 [Microphone     ]: USB-Audio - USB Microphone\n",
    )
    monkeypatch.setattr("argos.hardware.audio._list_capture_devices", lambda: ())

    assert select_available_input_device(("plughw:CARD=Microphone,DEV=0",)) == "plughw:CARD=Microphone,DEV=0"


def test_recorder_stop_raises_when_file_missing(monkeypatch, tmp_path):
    proc = FakeProc()
    proc.returncode = 1
    proc.stderr = None
    monkeypatch.setattr("argos.hardware.audio.WAV_PATH", str(tmp_path / "missing.wav"))
    monkeypatch.setattr("argos.hardware.audio.subprocess.Popen", lambda command, **kwargs: proc)
    recorder = Recorder("bad-device", 16000)

    recorder.start()
    try:
        recorder.stop()
    except RuntimeError as exc:
        assert "録音ファイルが作成されませんでした" in str(exc)
    else:
        raise AssertionError("RuntimeError が発生しませんでした")


def test_recorder_stop_accepts_arecord_sigint_message(monkeypatch, tmp_path):
    proc = FakeProc()
    proc.returncode = 1

    class FakeStderr:
        def read(self):
            return b"Aborted by signal Interrupt...\narecord: pcm_read:2272: read error: Interrupted system call"

    proc.stderr = FakeStderr()
    calls = []
    wav_path = tmp_path / "u.wav"
    monkeypatch.setattr("argos.hardware.audio.WAV_PATH", str(wav_path))
    monkeypatch.setattr("argos.hardware.audio.subprocess.Popen", lambda command, **kwargs: calls.append(command) or proc)
    recorder = Recorder("mic", 16000)

    recorder.start()
    written_path = _recording_path(calls[0])
    Path(written_path).write_bytes(b"RIFF" + b"0" * 200)

    assert recorder.stop() == written_path


def test_cleanup_stale_recordings_removes_old_temp_files(monkeypatch, tmp_path):
    """起動時掃除で古い録音一時ファイルだけ削除する。"""
    base_path = tmp_path / "utterance.wav"
    stale_path = tmp_path / "utterance-old.wav"
    fresh_path = tmp_path / "utterance-fresh.wav"
    other_path = tmp_path / "other.wav"
    stale_path.write_bytes(b"old")
    fresh_path.write_bytes(b"fresh")
    other_path.write_bytes(b"other")
    monkeypatch.setattr("argos.hardware.audio.WAV_PATH", str(base_path))
    monkeypatch.setattr("argos.hardware.audio.time.time", lambda: 10_000)
    old_mtime = 10_000 - 7200
    fresh_mtime = 10_000 - 30
    os.utime(stale_path, (old_mtime, old_mtime))
    os.utime(fresh_path, (fresh_mtime, fresh_mtime))
    os.utime(other_path, (old_mtime, old_mtime))

    assert cleanup_stale_recordings(max_age_seconds=3600) == 1
    assert not stale_path.exists()
    assert fresh_path.exists()
    assert other_path.exists()


def test_repair_wav_header_fixes_arecord_interrupted_size(tmp_path):
    """arecordの中断で残る過大なWAVフレーム数を修復する。"""
    wav_path = tmp_path / "broken.wav"
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes((1000).to_bytes(2, "little", signed=True) * 1600)
    with wav_path.open("r+b") as wav_file:
        wav_file.seek(4)
        wav_file.write((0x7FFFFFFF).to_bytes(4, "little"))
        wav_file.seek(40)
        wav_file.write((0x7FFFFFFF).to_bytes(4, "little"))

    with wave.open(str(wav_path), "rb") as wav_file:
        assert wav_file.getnframes() > 1_000_000

    _repair_wav_header(str(wav_path))

    with wave.open(str(wav_path), "rb") as wav_file:
        assert wav_file.getnframes() == 1600


def test_pad_wav_silence_adds_leading_and_trailing_frames(tmp_path):
    """短い発話向けの前後無音追加でWAVフレーム数が増える。"""
    wav_path = tmp_path / "short.wav"
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes((1000).to_bytes(2, "little", signed=True) * 1600)

    _pad_wav_silence(str(wav_path), leading_seconds=0.1, trailing_seconds=0.2)

    with wave.open(str(wav_path), "rb") as wav_file:
        assert wav_file.getnframes() == 1600 + 1600 + 3200


def test_audio_player_play_and_cancel(monkeypatch):
    proc = FakeProc()
    popen_calls = []
    run_calls = []
    monkeypatch.setattr("argos.hardware.audio.subprocess.Popen", lambda command, **kwargs: popen_calls.append(command) or proc)
    monkeypatch.setattr("argos.hardware.audio.subprocess.run", lambda command, **kwargs: run_calls.append(command))

    player = AudioPlayer("speaker", "card0", 80)
    player.play_wav(b"wav")
    player._proc = proc
    player.cancel()

    assert run_calls
    assert popen_calls[0] == ["aplay", "-q", "-D", "speaker", "-"]
    assert proc.input == b"wav"
    assert proc.terminated
    assert proc.wait_calls >= 1


def test_audio_player_set_volume_applies_amixer(monkeypatch):
    """音量変更時は0から100へ丸めてALSAへ反映する。"""
    run_calls = []
    monkeypatch.setattr("argos.hardware.audio.subprocess.run", lambda command, **kwargs: run_calls.append(command))

    player = AudioPlayer("speaker", "card0", 80)

    assert player.set_volume(120) == 100
    assert player.volume == 100
    assert run_calls[0] == ["amixer", "-q", "-D", "hw:CARD=card0", "set", "Master", "100%"]


def test_audio_player_set_volume_uses_default_mixer_without_card(monkeypatch):
    """出力カード未指定時はデフォルトミキサーへ音量を反映する。"""
    run_calls = []
    monkeypatch.setattr("argos.hardware.audio.subprocess.run", lambda command, **kwargs: run_calls.append(command))

    player = AudioPlayer("plughw:0,0", "", 80)

    assert player.set_volume(35) == 35
    assert run_calls[0] == ["amixer", "-q", "set", "Master", "35%"]


def test_audio_player_scales_wav_before_playback(monkeypatch):
    """plughw直指定でも効くように再生PCMへソフトウェア音量を掛ける。"""
    proc = FakeProc()
    monkeypatch.setattr("argos.hardware.audio.subprocess.Popen", lambda command, **kwargs: proc)
    monkeypatch.setattr("argos.hardware.audio.subprocess.run", lambda command, **kwargs: None)
    with BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes((10000).to_bytes(2, "little", signed=True))
        wav_data = buffer.getvalue()

    player = AudioPlayer("plughw:0,0", "", 25)
    player.play_wav(wav_data)

    sample = int.from_bytes(proc.input[:2], "little", signed=True)
    assert sample == 2500
