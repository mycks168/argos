"""Codex CLI クライアント。"""

from __future__ import annotations

import os
import json
import subprocess
import tempfile
from dataclasses import dataclass
from collections.abc import Generator
from pathlib import Path

from argos.config import CodexSlot, Settings


@dataclass
class CodexConversation:
    """Codex CLI の会話スロット状態。"""

    slot: CodexSlot
    started: bool = False


class CodexCliClient:
    """codex exec / codex exec resume を使って Codex と対話する。"""

    def __init__(self, settings: Settings) -> None:
        """設定から会話スロットを初期化する。"""
        self._settings = settings
        self._conversations = [CodexConversation(slot=slot) for slot in settings.codex_slots]
        self._index = 0

    @property
    def current_name(self) -> str:
        """現在の会話スロット名を返す。"""
        return self._conversations[self._index].slot.name

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
        try:
            command = self._build_command(conversation, output_path.name)
            env = self._build_env(conversation.slot)
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
                delta = _extract_text_delta(line, emitted)
                if not delta:
                    continue
                emitted += delta
                yield delta

            stderr = proc.stderr.read() if proc.stderr else ""
            return_code = proc.wait(timeout=10)
            if return_code != 0:
                raise RuntimeError(f"codex-cli エラー {return_code}: {stderr[-1000:]}")
            conversation.started = True
            text = Path(output_path.name).read_text(encoding="utf-8").strip()
            if text and not text.startswith(emitted):
                if emitted:
                    yield "\n" + text
                else:
                    yield text
            elif text:
                rest = text[len(emitted):]
                if rest:
                    yield rest
        finally:
            try:
                os.remove(output_path.name)
            except OSError:
                pass

    def reset_current(self) -> None:
        """現在のスロットを新規会話として扱う。"""
        self._conversations[self._index].started = False

    def _build_command(self, conversation: CodexConversation, output_path: str) -> list[str]:
        """Codex CLI のコマンドラインを構築する。"""
        slot = conversation.slot
        if conversation.started:
            base = ["codex", "exec", "resume", "--last", "--all", "--skip-git-repo-check"]
        else:
            base = ["codex", "exec", "--skip-git-repo-check", "-C", slot.cwd, "-s", self._settings.codex_sandbox]
        if slot.model:
            base.extend(["-m", slot.model])
        extra_args = list(self._settings.codex_extra_args)
        if "--json" not in extra_args:
            extra_args.append("--json")
        base.extend(extra_args)
        base.extend(["-o", output_path, "-"])
        return base

    def _build_env(self, slot: CodexSlot) -> dict[str, str]:
        """Codex 用の環境変数を作成する。"""
        env = os.environ.copy()
        if slot.codex_home:
            env["CODEX_HOME"] = slot.codex_home
        return env


def _extract_text_delta(line: str, emitted: str) -> str:
    """Codex JSONL イベントから未出力の応答テキストを取り出す。"""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return ""
    text = _extract_text(event)
    if not text:
        return ""
    if text.startswith(emitted):
        return text[len(emitted):]
    if text in emitted:
        return ""
    return text


def _extract_text(event: dict) -> str:
    """Codex イベントから読み上げ対象のテキストを取り出す。"""
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
