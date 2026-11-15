"""ストリーミング文字起こしの調査用に、録音と相槌の記録を残す。

相槌の頻度やタイミングを後から調べるため、設定で保存先を指定したときだけ、
発話の録音（WAV）と、届いた相槌などの記録（JSON）を同じ名前で保存する。
利用者の声が残るため既定では保存しない。保存先は本人だけが読めるようにし、
古いものから消して件数を抑える。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_KEEP = 50


def save_debug_recording(
    output_dir: str | Path,
    wav_path: str | Path,
    summary: dict[str, Any],
    kind: str,
    *,
    keep: int = DEFAULT_KEEP,
) -> Path | None:
    """録音と記録を保存し、保存した WAV のパスを返す。失敗したらログに出して None を返す。"""
    source = Path(wav_path)
    if not str(output_dir).strip() or not source.is_file():
        return None
    directory = Path(output_dir).expanduser()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        stem = f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}-{uuid.uuid4().hex[:8]}"
        target = directory / f"{stem}.wav"
        shutil.copyfile(source, target)
        os.chmod(target, 0o600)
        record = directory / f"{stem}.json"
        record.write_text(json.dumps({"kind": kind, **summary}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(record, 0o600)
    except OSError as exc:
        log.warning("調査用の録音を保存できませんでした: %s", exc)
        return None
    log.info("調査用の録音を保存しました: %s", target)
    _prune(directory, keep)
    return target


def _prune(directory: Path, keep: int) -> None:
    """保存した録音が keep 件を超えたら、古いものから WAV と記録をまとめて消す。"""
    recordings = sorted(directory.glob("*.wav"), key=lambda path: path.stat().st_mtime)
    for old in recordings[: max(0, len(recordings) - max(1, keep))]:
        for path in (old, old.with_suffix(".json")):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.warning("古い調査用の録音を消せませんでした: %s: %s", path, exc)
