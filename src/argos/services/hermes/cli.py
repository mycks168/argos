"""Hermes Agent CLI クライアント。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

from argos.config import AgentSlot, Settings


log = logging.getLogger(__name__)
SESSION_ID_PATTERN = re.compile(r"session[ _-]?id\s*[:=]\s*([A-Za-z0-9_.:-]+)", re.IGNORECASE)
HERMES_RESUME_ID_PATTERN = re.compile(r"\b\d{8}_\d{6}_[A-Za-z0-9]+\b")
HERMES_ERROR_LOG_PATH = Path("/tmp/argos/hermes-error.log")


@dataclass
class HermesConversation:
    """Hermes Agent CLI の会話スロット状態。"""

    slot: AgentSlot
    session_id: str = ""


class HermesSessionStore:
    """Hermesのsession IDをArgos管理ファイルに保存する。"""

    def __init__(self, path: Path) -> None:
        """保存先ファイルを保持する。"""
        self._path = path

    def load(self, key: str) -> str:
        """指定スロットの保存済みsession IDを返す。"""
        value = self._read().get(key, "")
        return value if isinstance(value, str) else ""

    def save(self, key: str, session_id: str) -> None:
        """指定スロットのsession IDを保存する。"""
        if not session_id:
            return
        data = self._read()
        if data.get(key) == session_id:
            return
        data[key] = session_id
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def clear(self, key: str) -> None:
        """指定スロットの保存済みsession IDを削除する。"""
        data = self._read()
        if key not in data:
            return
        del data[key]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read(self) -> dict[str, str]:
        """保存ファイルをJSONとして読み込む。"""
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError:
            log.exception("Hermes session IDの読み込みに失敗しました: %s", self._path)
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("Hermes session ID保存ファイルが壊れています: %s", self._path)
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): value for key, value in data.items() if isinstance(value, str)}


def _slot_key(slot: AgentSlot) -> str:
    """保存用にスロット設定から安定したキーを作る。"""
    raw = "\0".join((slot.name, slot.provider, slot.cwd))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class HermesCliClient:
    """Hermes Agent CLI を使って対話する。"""

    def __init__(self, settings: Settings) -> None:
        """設定から会話スロットを初期化する。"""
        self._settings = settings
        self._store = HermesSessionStore(Path(settings.agent_state_path).expanduser())
        self._conversations: list[HermesConversation] = []
        for slot in settings.agent_slots:
            if slot.provider.lower() != "hermes":
                raise ValueError(f"Hermesクライアントでは扱えないスロットです: {slot.name} provider={slot.provider}")
            stored_session_id = self._store.load(_slot_key(slot)) if settings.hermes_resume_saved else ""
            session_id = stored_session_id if _is_resume_session_id(stored_session_id) else ""
            if stored_session_id and not session_id:
                log.warning("Hermes resume用ではないsession IDを無視します: slot=%s value=%s", slot.name, stored_session_id)
                self._store.clear(_slot_key(slot))
            self._conversations.append(HermesConversation(slot=slot, session_id=session_id))
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
        """現在の会話スロットへプロンプトを送り、応答を返す。"""
        conversation = self._conversations[self._index]
        command = self._build_command(conversation, prompt)
        log.info("Hermes CLI 実行: slot=%s cwd=%s command=%s", conversation.slot.name, conversation.slot.cwd, command)
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=conversation.slot.cwd,
            env=os.environ.copy(),
        )
        stdout, stderr = proc.communicate()
        if stderr:
            _write_debug_log(HERMES_ERROR_LOG_PATH, stderr)
        if proc.returncode != 0:
            raise RuntimeError(f"hermes エラー {proc.returncode}: {stderr[-1000:]}")
        session_id = _extract_resume_session_id(stdout) or _load_latest_session_id(self._settings)
        if session_id:
            conversation.session_id = session_id
            self._store.save(_slot_key(conversation.slot), session_id)
        answer = _strip_session_info(stdout).strip()
        if not answer:
            raise RuntimeError("Hermes CLI から応答本文を取得できませんでした")
        yield answer

    def reset_current(self) -> None:
        """現在のスロットを新規会話として扱う。"""
        conversation = self._conversations[self._index]
        conversation.session_id = ""
        self._store.clear(_slot_key(conversation.slot))

    def _build_command(self, conversation: HermesConversation, prompt: str) -> list[str]:
        """Hermes CLI のコマンドラインを構築する。"""
        command = [self._settings.hermes_command, "chat", "-q", prompt, "-Q", "--source", self._settings.hermes_source]
        if self._settings.hermes_model:
            command.extend(["--model", self._settings.hermes_model])
        if self._settings.hermes_provider:
            command.extend(["--provider", self._settings.hermes_provider])
        if self._settings.hermes_toolsets:
            command.extend(["--toolsets", self._settings.hermes_toolsets])
        if self._settings.hermes_skills:
            command.extend(["--skills", self._settings.hermes_skills])
        if self._settings.hermes_pass_session_id:
            command.append("--pass-session-id")
        if conversation.session_id:
            command.extend(["--resume", conversation.session_id])
        command.extend(self._settings.hermes_extra_args)
        return command


def _extract_session_id(output: str) -> str:
    """Hermesの出力からsession IDらしい値を取り出す。"""
    match = SESSION_ID_PATTERN.search(output)
    return match.group(1).strip() if match else ""


def _extract_resume_session_id(output: str) -> str:
    """Hermesの--resumeへ渡せるsession IDを出力から取り出す。"""
    explicit = _extract_session_id(output)
    if _is_resume_session_id(explicit):
        return explicit
    match = HERMES_RESUME_ID_PATTERN.search(output)
    return match.group(0) if match else ""


def _is_resume_session_id(value: str) -> bool:
    """Hermesの--resumeへ渡せるsession ID形式ならTrueを返す。"""
    return bool(HERMES_RESUME_ID_PATTERN.fullmatch(value.strip()))


def _load_latest_session_id(settings: Settings) -> str:
    """Hermes session一覧から直近のresume用IDを取得する。"""
    for command in (
        [settings.hermes_command, "sessions", "list", "--source", settings.hermes_source, "--limit", "1"],
        [settings.hermes_command, "sessions", "list", "--limit", "1"],
    ):
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            log.exception("Hermes session一覧の取得に失敗しました: %s", command)
            continue
        if result.returncode != 0:
            continue
        session_id = _extract_resume_session_id(result.stdout)
        if session_id:
            return session_id
    return ""


def _strip_session_info(output: str) -> str:
    """読み上げ不要なsession ID行を取り除く。"""
    lines = [line for line in output.splitlines() if not SESSION_ID_PATTERN.search(line)]
    return "\n".join(lines)


def _write_debug_log(path: Path, text: str) -> None:
    """デバッグ用ログを保存する。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        log.exception("Hermes デバッグログの保存に失敗しました: %s", path)
