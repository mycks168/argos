"""調査用の録音保存のテスト。"""

from __future__ import annotations

import json
import os
import time

from argos.services.stt.debug_recording import save_debug_recording


def test_save_debug_recording_writes_wav_and_record(tmp_path):
    """WAV と記録を同じ名前で、本人だけが読める権限で保存する。"""
    wav = tmp_path / "u.wav"
    wav.write_bytes(b"RIFFdata")
    out = tmp_path / "debug"

    saved = save_debug_recording(out, wav, {"transcript": "こんにちは"}, "ptt")

    assert saved is not None and saved.read_bytes() == b"RIFFdata"
    record = json.loads(saved.with_suffix(".json").read_text(encoding="utf-8"))
    assert record == {"kind": "ptt", "transcript": "こんにちは"}
    assert oct(os.stat(out).st_mode & 0o777) == "0o700"
    assert oct(os.stat(saved).st_mode & 0o777) == "0o600"


def test_save_debug_recording_skips_without_dir_or_file(tmp_path):
    """保存先が空、または録音が無ければ保存しない。"""
    wav = tmp_path / "u.wav"
    wav.write_bytes(b"x")
    assert save_debug_recording(" ", wav, {}, "ptt") is None
    assert save_debug_recording(tmp_path / "d", tmp_path / "missing.wav", {}, "ptt") is None


def test_save_debug_recording_prunes_old_pairs(tmp_path):
    """件数を超えたら、古いものから WAV と記録をまとめて消す。"""
    wav = tmp_path / "u.wav"
    wav.write_bytes(b"x")
    out = tmp_path / "debug"
    first = save_debug_recording(out, wav, {}, "ptt", keep=2)
    old = time.time() - 100
    os.utime(first, (old, old))
    save_debug_recording(out, wav, {}, "ptt", keep=2)
    save_debug_recording(out, wav, {}, "ptt", keep=2)

    assert not first.exists() and not first.with_suffix(".json").exists()
    assert len(list(out.glob("*.wav"))) == 2
    assert len(list(out.glob("*.json"))) == 2


def test_save_debug_recording_reports_os_errors(tmp_path):
    """保存先を作れなければ None を返す。"""
    wav = tmp_path / "u.wav"
    wav.write_bytes(b"x")
    blocker = tmp_path / "file"
    blocker.write_text("not a dir")
    assert save_debug_recording(blocker / "sub", wav, {}, "ptt") is None
