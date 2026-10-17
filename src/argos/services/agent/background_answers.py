"""親ターンの入力ロックとは独立した、子エージェント回答の保存と回収。"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from argos.services.agent.claude_tmux_session import write_private_json

MAX_BACKGROUND_RECORDS = 512
MAX_BACKGROUND_TEXT_BYTES = 256 * 1024


@dataclass(frozen=True)
class BackgroundAnswer:
    """世代と子の識別子を保持し、異なる依頼の完了を混同しない。"""

    event_id: str
    generation: str
    agent_id: str
    agent_type: str
    text: str


class BackgroundAnswers:
    """完了通知の重複を除外し、CLI入力を塞がずに回答を配信する。"""

    def __init__(self, directory: Path) -> None:
        """既存の所有スロット内だけに機微な回答を保存する。"""
        self.directory = directory / "background"

    def record(
        self, generation: str, agent_id: str, agent_type: str, text: str
    ) -> None:
        """同じ子・本文の再通知を一つの完了として記録する。"""
        if not agent_id or not agent_type or not text.strip():
            return
        if len(text.encode()) > MAX_BACKGROUND_TEXT_BYTES:
            raise ValueError("子エージェントの回答が保存上限を超えています")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        event_id = hashlib.sha256(
            json.dumps([generation, agent_id, text]).encode()
        ).hexdigest()
        answer = BackgroundAnswer(event_id, generation, agent_id, agent_type, text)
        descriptor = os.open(
            self.directory / "record.lock", os.O_CREAT | os.O_RDWR, 0o600
        )
        with os.fdopen(descriptor, "a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            self._record_locked(answer)

    def _record_locked(self, answer: BackgroundAnswer) -> None:
        """同じ完了フックが並行しても保存先を壊さない。"""
        event_id = answer.event_id
        path = self.directory / f"{event_id}.json"
        if path.exists():
            return
        if len(list(self.directory.glob("*.json"))) >= MAX_BACKGROUND_RECORDS:
            raise RuntimeError(
                f"子エージェント回答の保存件数が上限です: {self.directory}"
            )
        write_private_json(path, json.dumps(asdict(answer), ensure_ascii=False))

    def pending(self, generation: str) -> tuple[str, ...]:
        """別世代と配信済み回答を除き、未配信の識別子だけ公開する。"""
        pending = []
        for path in sorted(
            self.directory.glob("*.json"), key=lambda item: item.stat().st_mtime_ns
        ):
            if (
                not path.with_suffix(".done").exists()
                and self._read(path).generation == generation
            ):
                pending.append(path.stem)
        return tuple(pending)

    @contextmanager
    def consume(self, event_id: str, generation: str) -> Iterator[BackgroundAnswer]:
        """回答別のロックを使い、モデルの次のターンは待たせない。"""
        if re.fullmatch(r"[a-f0-9]{64}", event_id) is None:
            raise ValueError("子エージェント回答の識別子が不正です")
        path = self.directory / f"{event_id}.json"
        with path.open("r") as source:
            try:
                fcntl.flock(source.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError(
                    "子エージェント回答は別の接続で回収中です"
                ) from error
            answer = self._read(path)
            if answer.generation != generation or path.with_suffix(".done").exists():
                raise ValueError("子エージェント回答は別世代または配信済みです")
            yield answer
            write_private_json(path.with_suffix(".done"), "{}")

    @staticmethod
    def _read(path: Path) -> BackgroundAnswer:
        """保存データの型を確認してから配信層へ渡す。"""
        fields = json.loads(path.read_text())
        if not isinstance(fields, dict) or not all(
            isinstance(value, str) for value in fields.values()
        ):
            raise ValueError(f"子エージェント回答の保存形式が不正です: {path}")
        return BackgroundAnswer(**fields)
