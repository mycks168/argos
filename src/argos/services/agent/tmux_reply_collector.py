"""フック回答を既存Runnerの完了ジョブへ引き渡す定期回収。"""

import fcntl
import json
import logging
import os
import threading
from pathlib import Path
from typing import Protocol

from argos.config import AgentSlot, Settings
from argos.services.agent.background_answers import BackgroundAnswer, BackgroundAnswers, BackgroundBusyError, BackgroundUnavailableError
from argos.services.agent.claude_tmux_client import ClaudeTmuxClient
from argos.services.agent.claude_tmux_session import write_private_json
from argos.services.agent.parent_replies import ParentReplies

log = logging.getLogger(__name__)
REPLY_POLL_INTERVAL_SECONDS = 1.0


class ReplySink(Protocol):
    """回収側がジョブ保存の具体実装に逆依存しないための受渡し契約。"""

    def accept_reply(self, slot: AgentSlot, answer: BackgroundAnswer) -> None:
        """同じ回答IDの再試行を一つの耐久的な完了ジョブとして保存する。"""


class TmuxReplyCollector:
    """CLIを起動せず、未回収回答だけを保存済みジョブへ移す。"""

    def __init__(self, settings: Settings, sink: ReplySink) -> None:
        """接続先ではなく、このRunnerが所有するローカルスロットだけ扱う。"""
        self.settings = settings
        self.sink = sink

    def poll(self) -> None:
        """一つのスロットの不調で他の回答回収を止めない。"""
        for slot in self.settings.agent_slots:
            if slot.slot_type == "remote" or slot.execution_mode != "tmux":
                continue
            try:
                self._collect_slot(slot)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                log.exception("tmux回答の定期回収に失敗しました: provider=%s", slot.provider)

    def _collect_slot(self, slot: AgentSlot) -> None:
        """通常ターン・リセットと同じロックを使い、応答を横取りしない。"""
        client = ClaudeTmuxClient(self.settings, slot)
        directory = client._session.directory
        descriptor = os.open(directory / "turn.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, "a") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            state = client._session.saved_state()
            if state is None or not self._is_ready(directory, state.session_id, slot.provider):
                return
            ParentReplies(directory, state.session_id).collect()
            store = BackgroundAnswers(directory)
            for event_id in client.background_ids():
                try:
                    self._forward(slot, store, event_id, state.session_id)
                except (BackgroundBusyError, BackgroundUnavailableError):
                    continue

    @staticmethod
    def _is_ready(directory: Path, generation: str, provider: str) -> bool:
        """接続確認のStopを利用者の後続回答として届けない。"""
        if provider in {"claude", "claudecode"}:
            return True
        native_path = directory / "native.json"
        if not native_path.exists():
            return False
        native = json.loads(native_path.read_text())
        return native.get("generation") == generation and native.get("is_ready") is True

    def _forward(self, slot: AgentSlot, store: BackgroundAnswers, event_id: str, generation: str) -> None:
        """ジョブの保存前には既読にせず、ブラウザとの回収競合も排他する。"""
        marker = store.directory / f"{event_id}.runner"
        if marker.exists():
            return
        with store.reserve(event_id, generation) as answer:
            self.sink.accept_reply(slot, answer)
            if answer.response_target == "local":
                store.acknowledge(event_id)
            write_private_json(marker, "{}")

    def start(self, stop: threading.Event | None = None) -> threading.Thread:
        """終了イベントを使い、サービスの停止を待たせずに定期回収する。"""
        shutdown = stop or threading.Event()

        def repeat() -> None:
            """一回ごとの障害をpollで隔離し、停止通知を即座に受け付ける。"""
            while not shutdown.is_set():
                self.poll()
                if shutdown.wait(REPLY_POLL_INTERVAL_SECONDS):
                    return

        thread = threading.Thread(target=repeat, name="tmux-reply-collector", daemon=True)
        thread.start()
        return thread
