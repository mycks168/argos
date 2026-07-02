"""Antigravity CLI クライアント。"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from argos.config import AgentSlot, Settings
from argos.services.agent.session_store import SlotSessionStore, slot_key


log = logging.getLogger(__name__)

ANTIGRAVITY_RAW_LOG_PATH = Path("/tmp/argos/antigravity-raw.log")
ANTIGRAVITY_ERROR_LOG_PATH = Path("/tmp/argos/antigravity-error.log")


@dataclass
class AntigravityConversation:
    """Antigravity CLI の会話スロット状態。"""

    slot: AgentSlot
    conversation_id: str = ""


@dataclass(frozen=True)
class AntigravityTranscriptSnapshot:
    """agy 実行前の transcript 状態。"""

    conversation_id: str
    path: Path | None
    line_count: int


class AntigravitySessionStore(SlotSessionStore):
    """Antigravity の会話IDをArgos管理ファイルに保存する。"""

    def __init__(self, path: Path) -> None:
        """保存先ファイルを初期化する。"""
        super().__init__(path, label="Antigravity 会話ID")


def _slot_key(slot: AgentSlot) -> str:
    """保存用にスロット設定から安定したキーを作る。"""
    return slot_key(slot)


class AntigravityCliClient:
    """agy CLI を使って Antigravity と対話する。"""

    def __init__(self, settings: Settings) -> None:
        """設定から会話スロットを初期化する。"""
        self._settings = settings
        self._store = AntigravitySessionStore(Path(settings.agent_state_path).expanduser())
        self._conversations: list[AntigravityConversation] = []
        for slot in settings.agent_slots:
            if slot.provider.lower() != "antigravity":
                raise ValueError(f"Antigravityクライアントでは扱えないスロットです: {slot.name} provider={slot.provider}")
            conversation_id = (
                self._store.load(_slot_key(slot))
                if settings.antigravity_continue_session and settings.antigravity_resume_saved
                else ""
            )
            log.info(
                "Antigravity セッション保存設定: slot=%s path=%s key=%s loaded=%s",
                slot.name,
                Path(settings.agent_state_path).expanduser(),
                _slot_key(slot)[:12],
                bool(conversation_id),
            )
            self._conversations.append(
                AntigravityConversation(
                    slot=slot, conversation_id=conversation_id
                )
            )
        self._index = 0

    @property
    def current_name(self) -> str:
        """現在の会話スロット名を返す。"""
        return self._conversations[self._index].slot.name

    @property
    def current_provider(self) -> str:
        """現在の会話スロットのprovider名を返す。"""
        return self._conversations[self._index].slot.provider

    def next_slot(self) -> str:
        """次の会話スロットへ切り替え、名前を返す。"""
        self._index = (self._index + 1) % len(self._conversations)
        return self.current_name

    def ask(self, prompt: str) -> str:
        """現在の会話スロットへプロンプトを送り、最終応答を返す。"""
        return "".join(self.ask_stream(prompt))

    def ask_stream(self, prompt: str) -> Generator[str, None, None]:
        """現在の会話スロットへプロンプトを送り、応答差分を順に返す。"""
        conversation = self._conversations[self._index]
        snapshot = _snapshot_transcript(self._settings, conversation)
        command = self._build_command(conversation, prompt)
        log.info("Antigravity CLI 実行: slot=%s cwd=%s command=%s", conversation.slot.name, conversation.slot.cwd, command)
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=conversation.slot.cwd,
            env=os.environ.copy(),
            bufsize=0,
        )
        stdout, stderr = proc.communicate()
        _write_debug_log(ANTIGRAVITY_RAW_LOG_PATH, stdout)
        _write_debug_log(ANTIGRAVITY_ERROR_LOG_PATH, stderr)
        if proc.returncode != 0:
            raise RuntimeError(f"antigravity-cli エラー {proc.returncode}: {stderr[-1000:]}")

        conversation_id = _load_last_conversation_id(self._settings, conversation.slot)
        if self._settings.antigravity_continue_session and conversation_id and conversation_id != conversation.conversation_id:
            conversation.conversation_id = conversation_id
            self._store.save(_slot_key(conversation.slot), conversation_id)

        transcript_path = _resolve_transcript_after_run(self._settings, conversation, snapshot, conversation_id)
        if transcript_path is None:
            raise RuntimeError("Antigravity transcript が見つかりません")
        start_line = snapshot.line_count if transcript_path == snapshot.path else 0
        answer = _extract_latest_done_planner_response(transcript_path, start_line)
        if not answer:
            raise RuntimeError("Antigravity transcript から今回の回答を取得できませんでした")
        yield answer

    def reset_current(self) -> None:
        """現在のスロットを新規会話として扱う。"""
        conversation = self._conversations[self._index]
        conversation.conversation_id = ""
        self._store.clear(_slot_key(conversation.slot))

    def _build_command(self, conversation: AntigravityConversation, prompt: str) -> list[str]:
        """Antigravity CLI のコマンドラインを構築する。"""
        command = [self._settings.antigravity_command]
        if self._settings.antigravity_skip_permissions:
            command.append("--dangerously-skip-permissions")
        if self._settings.antigravity_sandbox:
            command.append("--sandbox")
        if self._settings.antigravity_print_timeout:
            command.extend(["--print-timeout", self._settings.antigravity_print_timeout])
        if self._settings.antigravity_continue_session and conversation.conversation_id:
            command.extend(["--conversation", conversation.conversation_id])
        command.extend(self._settings.antigravity_extra_args)
        command.extend(["--print", f"{self._settings.antigravity_prompt_prefix}{prompt}"])
        return command


def _load_last_conversation_id(settings: Settings, slot: AgentSlot) -> str:
    """Antigravity の最新会話キャッシュからスロットの会話IDを取得する。"""
    cache_path = Path(settings.antigravity_home).expanduser() / "cache" / "last_conversations.json"
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    value = data.get(slot.cwd, "")
    return value if isinstance(value, str) else ""


def _snapshot_transcript(settings: Settings, conversation: AntigravityConversation) -> AntigravityTranscriptSnapshot:
    """agy実行前の会話IDとtranscript行数を記録する。"""
    conversation_id = conversation.conversation_id
    path = _find_transcript_path(settings, conversation_id) if conversation_id else None
    return AntigravityTranscriptSnapshot(
        conversation_id=conversation_id,
        path=path,
        line_count=_count_lines(path) if path else 0,
    )


def _resolve_transcript_after_run(
    settings: Settings,
    conversation: AntigravityConversation,
    snapshot: AntigravityTranscriptSnapshot,
    latest_conversation_id: str,
) -> Path | None:
    """agy実行後に読むべきtranscriptパスを返す。"""
    conversation_id = conversation.conversation_id or latest_conversation_id or snapshot.conversation_id
    return _find_transcript_path(settings, conversation_id)


def _find_transcript_path(settings: Settings, conversation_id: str) -> Path | None:
    """会話IDに対応するtranscriptファイルを返す。"""
    if not conversation_id:
        return None
    log_dir = Path(settings.antigravity_home).expanduser() / "brain" / conversation_id / ".system_generated" / "logs"
    for name in ("transcript_full.jsonl", "transcript.jsonl"):
        path = log_dir / name
        if path.exists():
            return path
    return None


def _count_lines(path: Path | None) -> int:
    """ファイルの行数を返す。"""
    if path is None:
        return 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as file:
            return sum(1 for _ in file)
    except (OSError, UnicodeError):
        return 0


def _extract_latest_done_planner_response(path: Path, start_line: int) -> str:
    """追加行の末尾から最新の完了済みPLANNER_RESPONSE本文を取り出す。"""
    entries = _read_jsonl_entries(path)
    for entry in reversed(entries[start_line:]):
        if _is_done_planner_response(entry):
            content = entry.get("content", "")
            return content if isinstance(content, str) else ""
    return ""


def _read_jsonl_entries(path: Path) -> list[dict[str, Any]]:
    """JSONLファイルを辞書のリストとして読み込む。"""
    entries: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as file:
            for line in file:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    entries.append(value)
    except (OSError, UnicodeError):
        return []
    return entries


def _is_done_planner_response(entry: dict[str, Any]) -> bool:
    """回答本文として扱える完了済みPLANNER_RESPONSEか判定する。"""
    return (
        entry.get("source") == "MODEL"
        and entry.get("type") == "PLANNER_RESPONSE"
        and entry.get("status") == "DONE"
        and isinstance(entry.get("content"), str)
        and bool(entry.get("content"))
    )


def _write_debug_log(path: Path, text: str) -> None:
    """デバッグ用にagyの出力を保存する。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        log.exception("Antigravity デバッグログの保存に失敗しました: %s", path)
