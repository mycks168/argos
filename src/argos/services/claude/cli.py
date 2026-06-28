"""Claude Code (claude) CLI クライアント。

このモジュールは、ARGOSからClaude Codeを非対話モード（プリントモード）で
安全かつセッションを維持して呼び出すためのクライアントを提供します。
"""

from __future__ import annotations

import os
import json
import hashlib
import logging
import subprocess
import uuid
from dataclasses import dataclass
from collections.abc import Generator
from pathlib import Path

from argos.config import AgentSlot, Settings

log = logging.getLogger(__name__)


@dataclass
class ClaudeConversation:
    """Claude CLI の会話スロット状態。"""

    slot: AgentSlot
    session_id: str = ""


class ClaudeSessionStore:
    """Claude セッションIDをArgosの管理ファイルに保存・管理するクラス。"""

    def __init__(self, path: Path) -> None:
        """保存先ファイルを初期化する。

        Args:
            path: セッションIDを保存するJSONファイルのパス
        """
        self._path = path

    def load(self, key: str) -> str:
        """指定スロットの保存済みセッションIDを取得する。

        Args:
            key: スロット識別キー
        Returns:
            セッションID（存在しない場合は空文字）
        """
        value = self._read().get(key, "")
        return value if isinstance(value, str) else ""

    def save(self, key: str, session_id: str) -> None:
        """指定スロットのセッションIDを保存する。

        Args:
            key: スロット識別キー
            session_id: 保存するセッションID
        """
        if not session_id:
            return
        data = self._read()
        if data.get(key) == session_id:
            return
        data[key] = session_id
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            log.exception("Claude セッションIDの保存に失敗しました: %s", self._path)

    def clear(self, key: str) -> None:
        """指定スロットの保存済みセッションIDを削除する。

        Args:
            key: スロット識別キー
        """
        data = self._read()
        if key not in data:
            return
        del data[key]
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            log.exception("Claude セッションIDの削除に失敗しました: %s", self._path)

    def _read(self) -> dict[str, str]:
        """保存ファイルをJSONとして読み込む。

        Returns:
            セッションID辞書
        """
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError:
            log.exception("Claude セッションIDの読み込みに失敗しました: %s", self._path)
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("Claude セッションID保存ファイルが壊れています: %s", self._path)
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): value for key, value in data.items() if isinstance(value, str)}


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
    raw = "\0".join((slot.name, slot.provider, slot.cwd))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        command = [
            "/home/yuki/.local/bin/claude",
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            # assistantのテキストをトークン単位の差分(stream_event/content_block_delta)で受け取るために必須
            "--include-partial-messages",
            # 確認ダイアログを出さずに自動で進めるためのオプション
            "--permission-mode", "bypassPermissions"
        ]

        if is_new_session:
            command.extend(["--session-id", conversation.session_id])
        else:
            command.extend(["--resume", conversation.session_id])

        command.append(prompt)

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
        # text_delta をそのまま中継する。これがトークン単位の本物のストリーミング差分。
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
                    yield text

            elif event_type == "result":
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

    def reset_current(self) -> None:
        """現在のスロットを新規会話として初期化する。"""
        conversation = self._conversations[self._index]
        conversation.session_id = ""
        self._store.clear(_slot_key(conversation.slot))
        log.info("スロット %s のセッションをリセットしました。", conversation.slot.name)
