from argos.hardware.audio import AudioPlayer, Recorder


class FakeProc:
    def __init__(self):
        self.signals = []
        self.killed = False
        self.terminated = False
        self.stdin = None
        self.stderr = None
        self.returncode = 0

    def poll(self):
        return None

    def send_signal(self, signal):
        self.signals.append(signal)

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True

    def terminate(self):
        self.terminated = True

    def communicate(self, input=None, timeout=None):
        self.input = input
        return b"", b""


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
    wav_path.write_bytes(b"RIFF" + b"0" * 200)
    assert recorder.stop().endswith("u.wav")

    recorder.start()
    recorder.cancel()
    assert proc.killed


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
    wav_path = tmp_path / "u.wav"
    monkeypatch.setattr("argos.hardware.audio.WAV_PATH", str(wav_path))
    monkeypatch.setattr("argos.hardware.audio.subprocess.Popen", lambda command, **kwargs: proc)
    recorder = Recorder("mic", 16000)

    recorder.start()
    wav_path.write_bytes(b"RIFF" + b"0" * 200)

    assert recorder.stop() == str(wav_path)


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
