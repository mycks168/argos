"""Codex CLI クライアント。"""

from __future__ import annotations

import os
import json
import hashlib
import logging
import subprocess
import tempfile
import time
from dataclasses import dataclass
from collections.abc import Generator
from pathlib import Path

from argos.config import AgentSlot, Settings
from argos.services.agent.session_store import SlotSessionStore, slot_key


log = logging.getLogger(__name__)


@dataclass
class CodexConversation:
    """Codex CLI の会話スロット状態。"""

    slot: AgentSlot
    started: bool = False
    session_id: str = ""


class CodexSessionStore(SlotSessionStore):
    """Codex セッションIDをArgos管理ファイルに保存する。"""

    def __init__(self, path: Path, fallback_paths: tuple[Path, ...] = ()) -> None:
        """保存先ファイルを初期化する。"""
        super().__init__(path, label="Codex セッションID", fallback_paths=fallback_paths)


def _session_store_path(settings: Settings) -> Path:
    """Argosのセッション保存ファイルを返す。"""
    return Path(settings.agent_state_path).expanduser()


def _legacy_session_store_paths(settings: Settings) -> tuple[Path, ...]:
    """旧CODEX_HOME配下のセッション保存候補を返す。"""
    homes = [
        settings.codex_home,
        os.environ.get("CODEX_HOME", ""),
        str(Path.home() / ".codex"),
    ]
    paths = []
    for home in homes:
        if home:
            path = Path(home).expanduser() / "argos-sessions.json"
            if path not in paths:
                paths.append(path)
    return tuple(paths)


def _slot_key(slot: AgentSlot) -> str:
    """保存用にスロット設定から安定したキーを作る。"""
    return slot_key(slot)


def _legacy_slot_key(slot: AgentSlot, settings: Settings) -> str:
    """旧Codexスロット形式の保存キーを作る。"""
    raw = "\0".join((slot.name, slot.cwd, settings.codex_home, settings.codex_model))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class CodexCliClient:
    """codex exec / codex exec resume を使って Codex と対話する。"""

    def __init__(self, settings: Settings) -> None:
        """設定から会話スロットを初期化する。"""
        self._settings = settings
        self._store = CodexSessionStore(_session_store_path(settings), _legacy_session_store_paths(settings))
        self._conversations: list[CodexConversation] = []
        for slot in settings.agent_slots:
            if slot.provider.lower() != "codex":
                raise ValueError(f"Codexクライアントでは扱えないスロットです: {slot.name} provider={slot.provider}")
            store_path = _session_store_path(settings)
            slot_key = _slot_key(slot)
            session_id = self._store.load(slot_key) or self._store.load(_legacy_slot_key(slot, settings))
            log.info(
                "Codex セッション保存設定: slot=%s path=%s key=%s loaded=%s",
                slot.name,
                store_path,
                slot_key[:12],
                bool(session_id),
            )
            self._conversations.append(CodexConversation(slot=slot, session_id=session_id))
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
        output_path = tempfile.NamedTemporaryFile(prefix="argos-codex-", suffix=".txt", delete=False)
        output_path.close()
        started_at = time.time()
        # "stream"(既定)は途中経過を逐次読み上げる。"final"は途中経過を読み上げず、
        # 完了後のまとめだけを1回読み上げる。Codexの完了後の出力は途中経過の続きではなく
        # 全体を再構成したまとめのことがあり、両方読み上げると同じ内容を2回話してしまうため。
        stream_mode = self._settings.codex_stream_mode != "final"
        try:
            command = self._build_command(conversation, output_path.name)
            env = self._build_env(conversation.slot)
            store_path = _session_store_path(self._settings)
            log.info(
                "Codex CLI 実行: slot=%s cwd=%s codex_home=%s store=%s command=%s",
                conversation.slot.name,
                conversation.slot.cwd,
                env.get("CODEX_HOME", ""),
                store_path,
                command,
            )
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=conversation.slot.cwd,
                env=env,
            )
            assert proc.stdin is not None
            assert proc.stdout is not None
            proc.stdin.write(prompt)
            proc.stdin.close()

            emitted = ""
            for line in proc.stdout:
                event = _load_event(line)
                session_id = _extract_session_id(event)
                if session_id and session_id != conversation.session_id:
                    conversation.session_id = session_id
                    log.info(
                        "Codex セッションIDを保存: slot=%s session_id=%s store=%s",
                        conversation.slot.name,
                        session_id,
                        store_path,
                    )
                    self._store.save(_slot_key(conversation.slot), session_id)
                delta = _extract_text_delta(event, emitted)
                if not delta:
                    continue
                emitted += delta
                if stream_mode:
                    yield delta

            stderr = proc.stderr.read() if proc.stderr else ""
            return_code = proc.wait(timeout=10)
            if return_code != 0:
                raise RuntimeError(f"codex-cli エラー {return_code}: {stderr[-1000:]}")
            if not conversation.session_id:
                session_id = _load_recent_session_id(conversation.slot, self._settings, started_at)
                if session_id:
                    conversation.session_id = session_id
                    log.info(
                        "Codex セッションIDを保存: slot=%s session_id=%s store=%s source=session-file",
                        conversation.slot.name,
                        session_id,
                        store_path,
                    )
                    self._store.save(_slot_key(conversation.slot), session_id)
            conversation.started = True
            text = Path(output_path.name).read_text(encoding="utf-8").strip()
            if not stream_mode:
                # finalモード: 途中経過は読み上げず、完了後のまとめだけを1回返す。
                if text:
                    yield text
            elif not emitted and text:
                # streamモードで途中経過が一切取れなかった場合のフォールバック。
                yield text
        finally:
            try:
                os.remove(output_path.name)
            except OSError:
                pass

    def reset_current(self) -> None:
        """現在のスロットを新規会話として扱う。"""
        conversation = self._conversations[self._index]
        conversation.started = False
        conversation.session_id = ""
        self._store.clear(_slot_key(conversation.slot))

    def _build_command(self, conversation: CodexConversation, output_path: str) -> list[str]:
        """Codex CLI のコマンドラインを構築する。"""
        slot = conversation.slot
        if conversation.session_id:
            base = self._exec_base()
            base.extend(["resume", "--all", "--skip-git-repo-check"])
            prompt_args = [conversation.session_id, "-"]
        elif conversation.started:
            base = self._exec_base()
            base.extend(["resume", "--last", "--all", "--skip-git-repo-check"])
            prompt_args = ["-"]
        else:
            base = self._exec_base()
            base.extend(["--skip-git-repo-check", "-C", slot.cwd])
            if not self._settings.codex_bypass_sandbox:
                base.extend(["-s", self._settings.codex_sandbox])
            prompt_args = ["-"]
        if self._settings.codex_model:
            base.extend(["-m", self._settings.codex_model])
        extra_args = list(self._settings.codex_extra_args)
        if "--json" not in extra_args:
            extra_args.append("--json")
        base.extend(extra_args)
        base.extend(["-o", output_path])
        base.extend(prompt_args)
        return base

    def _exec_base(self) -> list[str]:
        """Codex exec の共通起動オプションを返す。"""
        base = ["codex", "exec"]
        if self._settings.codex_bypass_sandbox:
            base.append("--dangerously-bypass-approvals-and-sandbox")
        return base

    def _build_env(self, slot: AgentSlot) -> dict[str, str]:
        """Codex 用の環境変数を作成する。"""
        env = os.environ.copy()
        if self._settings.codex_home:
            env["CODEX_HOME"] = self._settings.codex_home
        return env


def _load_event(line: str) -> dict:
    """Codex JSONL の1行をイベント辞書として読み込む。"""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return {}
    return event if isinstance(event, dict) else {}


def _extract_session_id(event: dict) -> str:
    """Codex イベントからセッションIDを取り出す。

    現行の `codex exec --json` は起動時に `thread.started` で `thread_id` を返す。
    旧バージョンの `session_meta`（`~/.codex/sessions/` 配下の保存ファイル形式）も
    念のため互換対応する。
    """
    if event.get("type") == "thread.started":
        value = event.get("thread_id", "")
        return value if isinstance(value, str) else ""
    if event.get("type") == "session_meta":
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            return ""
        value = payload.get("id", "")
        return value if isinstance(value, str) else ""
    return ""


def _load_recent_session_id(slot: AgentSlot, settings: Settings, started_at: float) -> str:
    """直近に更新された Codex セッションファイルからIDを取り出す。"""
    codex_home = settings.codex_home or os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    sessions_dir = Path(codex_home).expanduser() / "sessions"
    if not sessions_dir.exists():
        return ""
    try:
        files = sorted(sessions_dir.rglob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        log.exception("Codex セッションファイル一覧の取得に失敗しました: %s", sessions_dir)
        return ""
    for path in files[:20]:
        try:
            if path.stat().st_mtime < started_at - 1:
                continue
            first_line = path.read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError):
            continue
        event = _load_event(first_line)
        if event.get("type") != "session_meta":
            continue
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if payload.get("cwd") != slot.cwd:
            continue
        session_id = _extract_session_id(event)
        if session_id:
            log.info("Codex セッションファイル検出: slot=%s file=%s", slot.name, path)
            return session_id
    return ""


def _extract_text_delta(event: dict, emitted: str) -> str:
    """Codex イベントから未出力の応答テキストを取り出す。"""
    text = _extract_text(event)
    if not text:
        return ""
    if text.startswith(emitted):
        return text[len(emitted):]
    if text in emitted:
        return ""
    return text


def _extract_text(event: dict) -> str:
    """Codex イベントから読み上げ対象のテキストを取り出す。

    現行の `codex exec --json` は `item.completed`（`item.type == "agent_message"`）で
    完成済みのエージェント発話を1件ずつ返す。`command_execution` など他のitem種別は
    読み上げ対象に含めない。旧バージョンの `event_msg`/`response_item` 形式も
    念のため互換対応する。
    """
    if event.get("type") == "item.completed":
        item = event.get("item", {})
        if isinstance(item, dict) and item.get("type") == "agent_message":
            return str(item.get("text", ""))
        return ""
    payload = event.get("payload", {})
    if event.get("type") == "event_msg" and payload.get("type") == "agent_message":
        if payload.get("phase") in ("final_answer", None):
            return str(payload.get("message", ""))
    if event.get("type") == "event_msg" and payload.get("type") == "task_complete":
        return str(payload.get("last_agent_message", ""))
    if event.get("type") == "response_item":
        item = payload
        if item.get("type") == "message" and item.get("role") == "assistant":
            parts = []
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    parts.append(str(content.get("text", "")))
            return "".join(parts)
    return ""
