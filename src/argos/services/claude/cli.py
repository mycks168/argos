"""Claude Code (claude) CLI クライアント。

このモジュールは、ARGOSからClaude Codeを非対話モード（プリントモード）で
安全かつセッションを維持して呼び出すためのクライアントを提供します。
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import uuid
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

from argos.config import (
    AgentSlot,
    Settings,
    resolve_agent_slot_command,
    resolve_agent_slot_extra_args,
    resolve_agent_slot_model,
)
from argos.services.agent.session_store import SlotSessionStore, slot_key

log = logging.getLogger(__name__)


@dataclass
class ClaudeConversation:
    """Claude CLI の会話スロット状態。"""

    slot: AgentSlot
    session_id: str = ""


class ClaudeSessionStore(SlotSessionStore):
    """Claude セッションIDをArgosの管理ファイルに保存・管理するクラス。"""

    def __init__(self, path: Path) -> None:
        """保存先ファイルを初期化する。

        Args:
            path: セッションIDを保存するJSONファイルのパス
        """
        super().__init__(path, label="Claude セッションID")


def _session_store_path(settings: Settings) -> Path:
    """Argosのセッション保存ファイルを返す。

    Args:
        settings: ARGOS設定
    Returns:
        保存先ファイルのPathオブジェクト
    """
    return Path(settings.agent_state_path).expanduser()


def _slot_key(slot: AgentSlot) -> str:
    """保存用にスロット設定から一意なキーを作成する。

    Args:
        slot: エージェントのスロット情報
    Returns:
        ハッシュ化されたキー文字列
    """
    return slot_key(slot)


class ClaudeCliClient:
    """claude コマンドの非対話実行（-p）を用いて対話を行うクライアントクラス。"""

    def __init__(self, settings: Settings) -> None:
        """設定から会話スロットとセッションストアを初期化する。

        Args:
            settings: ARGOS設定
        """
        self._settings = settings
        self._store = ClaudeSessionStore(_session_store_path(settings))
        self._conversations: list[ClaudeConversation] = []
        for slot in settings.agent_slots:
            if slot.provider.lower() not in {"claude", "claudecode"}:
                raise ValueError(f"Claudeクライアントでは扱えないスロットです: {slot.name} provider={slot.provider}")
            slot_key = _slot_key(slot)
            session_id = self._store.load(slot_key)
            log.info(
                "Claude セッション保存設定: slot=%s path=%s key=%s loaded=%s",
                slot.name,
                _session_store_path(settings),
                slot_key[:12],
                bool(session_id),
            )
            self._conversations.append(ClaudeConversation(slot=slot, session_id=session_id))
        self._index = 0

    @property
    def current_name(self) -> str:
        """現在の会話スロット名を返す。"""
        return self._conversations[self._index].slot.name

    @property
    def current_provider(self) -> str:
        """現在の会話スロットのprovider名を返す。"""
        return self._conversations[self._index].slot.provider

    @property
    def current_model(self) -> str:
        """現在スロットで指定するモデルを返す。"""
        return resolve_agent_slot_model(self._settings, self._conversations[self._index].slot)

    def next_slot(self) -> str:
        """次の会話スロットへ切り替え、名前を返す。

        Returns:
            切り替え後のスロット名
        """
        self._index = (self._index + 1) % len(self._conversations)
        return self.current_name

    def ask(self, prompt: str) -> str:
        """現在の会話スロットへプロンプトを送り、最終応答を返す。

        Args:
            prompt: 送信するプロンプト
        Returns:
            エージェントからの最終応答テキスト
        """
        return "".join(self.ask_stream(prompt))

    def ask_stream(self, prompt: str) -> Generator[str, None, None]:
        """現在の会話スロットへプロンプトを送り、応答の差分を順次生成して返す。

        Args:
            prompt: 送信するプロンプト
        Yields:
            応答テキストの差分（チャンク）
        """
        conversation = self._conversations[self._index]
        slot_key = _slot_key(conversation.slot)
        
        # 履歴が存在するかのフラグ判定
        is_new_session = False
        if not conversation.session_id:
            conversation.session_id = str(uuid.uuid4())
            is_new_session = True
            log.info("新規のセッションIDを生成しました: %s", conversation.session_id)
            self._store.save(slot_key, conversation.session_id)

        # コマンドの構築
        configured_command = resolve_agent_slot_command(self._settings, conversation.slot)
        discovered_command = shutil.which(configured_command)
        default_user_command = str(Path.home() / ".local/bin/claude")
        claude_command = discovered_command or (default_user_command if configured_command == "claude" else configured_command)
        command = [
            claude_command,
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            # assistantのテキストをトークン単位の差分(stream_event/content_block_delta)で受け取るために必須
            "--include-partial-messages",
            # 確認ダイアログを出さずに自動で進めるためのオプション
            "--permission-mode", "bypassPermissions"
        ]

        model = resolve_agent_slot_model(self._settings, conversation.slot)
        if model:
            command.extend(["--model", model])

        command.extend(resolve_agent_slot_extra_args(self._settings, conversation.slot))

        if is_new_session:
            command.extend(["--session-id", conversation.session_id])
        else:
            command.extend(["--resume", conversation.session_id])

        # 発話がハイフンで始まってもClaude CLIのオプションとして解釈させない。
        command.extend(["--", prompt])

        log.info(
            "Claude CLI 実行: slot=%s cwd=%s session_id=%s command=%s",
            conversation.slot.name,
            conversation.slot.cwd,
            conversation.session_id,
            command,
        )

        # サブプロセスの実行
        # stdin=subprocess.DEVNULL を指定して完全に非対話（non-interactive）として認識させる
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=conversation.slot.cwd,
        )

        assert proc.stdout is not None

        # --include-partial-messages により届く stream_event/content_block_delta の
        # text_delta を中継する。差分が来ない場合はresult本文を完了時に返す。
        streamed_text = ""
        result_text = ""
        for line in proc.stdout:
            try:
                event = json.loads(line.strip())
            except json.JSONDecodeError:
                continue

            event_type = event.get("type")

            if event_type == "stream_event":
                inner = event.get("event", {})
                if inner.get("type") != "content_block_delta":
                    continue
                delta = inner.get("delta", {})
                if delta.get("type") != "text_delta":
                    continue
                text = delta.get("text", "")
                if text:
                    streamed_text += text
                    yield text

            elif event_type == "result":
                result_text = str(event.get("result", "") or "")
                log.info(
                    "Claudeジョブが正常に完了しました。Cost: %s, Usage: %s",
                    event.get("total_cost_usd"),
                    event.get("usage"),
                )

        stderr = proc.stderr.read() if proc.stderr else ""
        return_code = proc.wait()
        
        if return_code != 0:
            # エラー発生時はセッションをクリアして例外を投げる
            log.error("claude-cli 実行エラー %s: %s", return_code, stderr)
            self.reset_current()
            raise RuntimeError(f"claude-cli エラー {return_code}: {stderr[-1000:]}")

        if not streamed_text and result_text:
            yield result_text

    def reset_current(self) -> None:
        """現在のスロットを新規会話として初期化する。"""
        conversation = self._conversations[self._index]
        conversation.session_id = ""
        self._store.clear(_slot_key(conversation.slot))
        log.info("スロット %s のセッションをリセットしました。", conversation.slot.name)
