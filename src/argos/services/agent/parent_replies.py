"""通常ターンが引き取らなかった親Stopを、耐久的な回答待ちへ移す。"""

import hashlib
import json
from pathlib import Path

from argos.services.agent.background_answers import BackgroundAnswers
from argos.services.agent.claude_tmux_events import read_events
from argos.services.agent.claude_tmux_session import write_private_json

class ParentReplies:
    """台帳位置と通常応答の引取記録で、二つの回収経路を競合させない。"""

    def __init__(self, directory: Path, generation: str) -> None:
        """世代ごとの読込位置をスロットの所有ディレクトリ内に置く。"""
        self.directory = directory
        self.generation = generation
        self.cursor_path = directory / "parent-cursor.json"

    def initialize(self, offset: int) -> int:
        """初回だけ基準位置を保存し、再起動では既読位置を維持する。"""
        if self.cursor_path.exists():
            saved = json.loads(self.cursor_path.read_text())
            if saved["generation"] == self.generation:
                return int(saved["offset"])
        self._advance(offset)
        return offset

    def taken(self, end_offset: int) -> None:
        """通常応答の引取りは個別に記録し、間の未回収回答を飛ばさない。"""
        marker = self._marker(end_offset)
        marker.parent.mkdir(mode=0o700, exist_ok=True)
        write_private_json(marker, "{}")

    def collect(self) -> None:
        """通常ターンの排他中に呼び、保存できた位置までだけ読み進める。"""
        journal = self.directory / "events.jsonl"
        end = journal.stat().st_size if journal.exists() else 0
        offset = self.initialize(end)
        events, _end = read_events(journal, offset)
        for event in events:
            is_answer = event.session_id == self.generation and event.event == "Stop" and bool(event.text.strip())
            marker = self._marker(event.end_offset)
            if is_answer and not marker.exists():
                if self._is_reserved(event.end_offset):
                    return
                BackgroundAnswers(self.directory).record(self.generation, f"parent:{event.end_offset}", "parent",
                    event.text)
            self._advance(event.end_offset)
            marker.unlink(missing_ok=True)

    def _is_reserved(self, end_offset: int) -> bool:
        """まだ通常経路で回収できる入力の最初のStopは横取りしない。"""
        for name in ("pending.json", "manual.json", "completed-manual.json"):
            path = self.directory / name
            if not path.exists():
                continue
            pending = json.loads(path.read_text())
            if pending.get("session_id", self.generation) != self.generation:
                continue
            if pending.get("is_submitted", True) and int(pending["offset"]) < end_offset:
                return True
        return False

    def _marker(self, end_offset: int) -> Path:
        """外部入力の世代文字列をファイルパスとして解釈させない。"""
        identity = hashlib.sha256(f"{self.generation}:{end_offset}".encode()).hexdigest()
        return self.directory / "parent-taken" / f"{identity}.json"

    def _advance(self, offset: int) -> None:
        """転送前のクラッシュでは同じ回答IDで再試行できる位置を残す。"""
        write_private_json(self.cursor_path, json.dumps({"generation":self.generation, "offset":offset}))
