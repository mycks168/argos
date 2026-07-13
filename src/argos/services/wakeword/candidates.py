"""ウェイクワード誤検知候補の保存。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def save_false_positive_candidate(
    wav_path: str | Path,
    output_dir: str | Path,
    *,
    reason: str,
    transcript: str = "",
    rms: float | None = None,
    detected_at: datetime | None = None,
) -> Path:
    """破棄した音声と判定情報を学習用hard negative候補として保存する。"""
    source = Path(wav_path)
    if not source.is_file():
        raise FileNotFoundError(f"誤検知候補の録音ファイルが見つかりません: {source}")

    timestamp = detected_at or datetime.now(timezone.utc)
    session_name = f"false-positive-{timestamp:%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    session_dir = Path(output_dir).expanduser() / "hard_negative" / session_name
    session_dir.mkdir(parents=True, exist_ok=False)

    target = session_dir / "sample.wav"
    shutil.copy2(source, target)
    metadata = {
        "created_at": timestamp.isoformat(),
        "reason": reason,
        "transcript": transcript,
        "rms": rms,
        "source_name": source.name,
    }
    (session_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return session_dir
