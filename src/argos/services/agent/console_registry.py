"""定義済みスロットだけを端末へ接続する登録簿。"""

from __future__ import annotations

import base64
import threading
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from argos.config import AgentSlot, Settings
from argos.agent_slot_options import is_tmux_provider
from argos.services.agent.claude_tmux_client import ClaudeTmuxClient
from argos.services.agent.claude_tmux_session import run_tmux
from argos.services.agent.console_protocol import (
    CloseConsole, ConnectionId, ConsoleAck, ConsoleCommand, ConsoleOpened, ConsoleRead,
    ConsoleResponse, OpenConsole, ReadConsole, ResizeConsole, WriteConsole,
)
from argos.services.agent.remote_console import RemoteConsole, remote_execution_mode
from argos.services.agent.remote_argos import RemoteArgosClient
from argos.services.agent.terminal_pty import TerminalPty
from argos.services.agent.tmux_transport import TerminalSize


MAX_CONNECTIONS = 16
REMOTE_MODE_CACHE_SECONDS = 60


class ConsoleConnection(Protocol):
    """ローカルとリモートの端末搬送を同じ契約で扱う。"""

    @property
    def is_closed(self) -> bool:
        """接続が終了しているか返す。"""

    def read(self, offset: int) -> ConsoleRead:
        """端末出力を返す。"""

    def write(self, data: bytes) -> None:
        """生のキー入力を送る。"""

    def resize(self, size: TerminalSize) -> None:
        """端末の寸法を変更する。"""

    def close(self) -> None:
        """エージェントを終了させず切断する。"""


@dataclass
class LocalConsole:
    """会話の排他制御とPTYを結び付ける。"""

    terminal: TerminalPty
    client: ClaudeTmuxClient

    @property
    def is_closed(self) -> bool:
        """PTYの終了を返す。"""
        return self.terminal.is_closed

    def read(self, offset: int) -> ConsoleRead:
        """UTF-8途中の切断を壊さない形式へ変換する。"""
        chunk = self.terminal.read(offset)
        return ConsoleRead(chunk.offset, base64.b64encode(chunk.data).decode("ascii"), chunk.is_closed, self.client.has_pending_manual_turn())

    def write(self, data: bytes) -> None:
        """音声処理中には生のキー入力を混入させない。"""
        self.client.console_input(data, lambda: self.terminal.write(data))

    def resize(self, size: TerminalSize) -> None:
        """実attachクライアントの寸法を更新する。"""
        self.terminal.resize(size)

    def close(self) -> None:
        """attachだけを切断する。"""
        self.terminal.close()


class ConsoleRegistry:
    """認証済みAPIに渡す接続IDを管理する。"""

    def __init__(self, settings: Settings) -> None:
        """任意のsession名ではなく定義済みスロットを操作対象にする。"""
        self._settings = settings
        self._connections: dict[ConnectionId, ConsoleConnection] = {}
        self._lock = threading.Lock()
        self._remote_modes: dict[str, tuple[float, str]] = {}

    def execution_mode(self, name: str, provider: str) -> str:
        """接続先の旧版互換性を維持しながら表示方式を取得する。"""
        slot = self._find_slot(name, provider)
        mode = slot.execution_mode
        if slot.slot_type == "remote":
            key = f"{provider}\0{name}"
            cached = self._remote_modes.get(key)
            if cached is None or time.monotonic() - cached[0] > REMOTE_MODE_CACHE_SECONDS:
                cached = (time.monotonic(), remote_execution_mode(slot))
                self._remote_modes[key] = cached
            mode = cached[1]
        return mode

    def execute(self, command: ConsoleCommand) -> ConsoleResponse:
        """スキーマで検証済みの操作だけを実行する。"""
        if isinstance(command, OpenConsole):
            return self._open(command)
        with self._lock:
            connection = self._connections.get(command.connection_id)
        if connection is None:
            raise KeyError("端末接続がありません。再接続してください")
        return self._execute_connected(command, connection)

    def close(self) -> None:
        """ARGOS終了時にPTYだけを回収する。"""
        with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()
        for connection in connections:
            connection.close()

    def observe(self, name: str, provider: str) -> Iterable[str]:
        """既に端末から送ったターンを、会話・読み上げ経路へ接続する。"""
        slot = self._find_slot(name, provider)
        if self.execution_mode(name, provider) != "tmux":
            raise ValueError("このスロットに端末からの送信はありません")
        if slot.slot_type == "remote":
            yield from RemoteArgosClient(self._settings, slot).observe_stream()
        else:
            yield from ClaudeTmuxClient(self._settings, slot).observe_stream()

    def _execute_connected(self, command: ReadConsole | WriteConsole | ResizeConsole | CloseConsole, connection: ConsoleConnection) -> ConsoleResponse:
        """読込をロック外で待ち、入力や他スロットを停止させない。"""
        if isinstance(command, ReadConsole):
            return connection.read(command.offset)
        if isinstance(command, WriteConsole):
            connection.write(command.data)
        elif isinstance(command, ResizeConsole):
            connection.resize(command.size)
        else:
            with self._lock:
                self._connections.pop(command.connection_id, None)
            connection.close()
        return ConsoleAck()

    def _open(self, command: OpenConsole) -> ConsoleOpened:
        """未知のセッションや従来スロットをシェルとして開かない。"""
        slot = self._find_slot(command.name, command.provider)
        with self._lock:
            self._connections = {key: value for key, value in self._connections.items() if not value.is_closed}
            if len(self._connections) >= MAX_CONNECTIONS:
                raise RuntimeError("端末接続数の上限に達しています")
            connection: ConsoleConnection
            if slot.slot_type == "remote":
                connection = RemoteConsole(slot, command.size)
            else:
                if slot.execution_mode != "tmux" or not is_tmux_provider(slot.provider):
                    raise ValueError("このスロットはtmux方式ではありません")
                client = ClaudeTmuxClient(self._settings, slot)
                target = client.terminal_target()
                run_tmux(["set-option", "-t", target.pane_id, "prefix", "None"])
                run_tmux(["set-option", "-t", target.pane_id, "prefix2", "None"])
                run_tmux(["set-window-option", "-t", target.pane_id, "window-size", "latest"])
                terminal = TerminalPty(("tmux", "attach-session", "-t", f"={target.session_name}"), command.size)
                connection = LocalConsole(terminal, client)
            connection_id = ConnectionId(str(uuid.uuid4()))
            self._connections[connection_id] = connection
            return ConsoleOpened(connection_id)

    def _find_slot(self, name: str, provider: str) -> AgentSlot:
        """URLやsession名を利用者入力から組み立てない。"""
        slot = next((slot for slot in self._settings.agent_slots if (slot.name, slot.provider) == (name, provider)), None)
        if slot is None:
            raise ValueError("定義されたスロットが見つかりません")
        return slot
