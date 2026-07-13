import json
from datetime import datetime, timezone

import pytest

from argos.services.wakeword.candidates import save_false_positive_candidate


def test_save_false_positive_candidate_writes_audio_and_metadata(tmp_path):
    """誤検知候補の音声と判定情報をtrainer互換の配置へ保存する。"""
    source = tmp_path / "recording.wav"
    source.write_bytes(b"RIFF-test-audio")
    detected_at = datetime(2026, 7, 11, 1, 2, 3, tzinfo=timezone.utc)

    saved_dir = save_false_positive_candidate(
        source,
        tmp_path / "candidates",
        reason="wakeword_missing",
        transcript="別の会話",
        rms=123.4,
        detected_at=detected_at,
    )

    assert saved_dir.parent == tmp_path / "candidates" / "hard_negative"
    assert saved_dir.name.startswith("false-positive-20260711T010203Z-")
    assert (saved_dir / "sample.wav").read_bytes() == source.read_bytes()
    metadata = json.loads((saved_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata == {
        "created_at": "2026-07-11T01:02:03+00:00",
        "reason": "wakeword_missing",
        "transcript": "別の会話",
        "rms": 123.4,
        "source_name": "recording.wav",
    }


def test_save_false_positive_candidate_rejects_missing_audio(tmp_path):
    """元音声が無い場合は空の候補ディレクトリを作らず失敗する。"""
    with pytest.raises(FileNotFoundError, match="録音ファイルが見つかりません"):
        save_false_positive_candidate(
            tmp_path / "missing.wav",
            tmp_path / "candidates",
            reason="stt_empty",
        )

    assert not (tmp_path / "candidates").exists()
