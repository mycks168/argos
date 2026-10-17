"""端末搬送の入力スキーマを既存の会話APIから分離する。"""

from __future__ import annotations

import base64
from collections.abc import Iterable
from dataclasses import dataclass
from typing import NewType

from argos.services.agent.tmux_transport import TerminalSize


ConnectionId = NewType("ConnectionId", str)
MAX_KEY_BYTES = 64 * 1024


@dataclass(frozen=True)
class ObservedTerminalInput:
    """端末に送信済みの入力と、再送不要の回答取得元。"""

    transcript: str
    stream: Iterable[str]


def split_terminal_input(value: str | ObservedTerminalInput | None) -> tuple[str | None, Iterable[str] | None]:
    """送信済みの端末入力を通常のプロンプトと区別する。"""
    if isinstance(value, ObservedTerminalInput):
        return value.transcript, value.stream
    return value, None


@dataclass(frozen=True)
class OpenConsole:
    """定義済みスロットへの接続要求。"""

    name: str
    provider: str
    size: TerminalSize


@dataclass(frozen=True)
class ReadConsole:
    """再送できる読込位置を持つ出力要求。"""

    connection_id: ConnectionId
    offset: int


@dataclass(frozen=True)
class WriteConsole:
    """UTF-8や制御文字を変換しない入力要求。"""

    connection_id: ConnectionId
    data: bytes


@dataclass(frozen=True)
class ResizeConsole:
    """接続したクライアントのサイズ変更要求。"""

    connection_id: ConnectionId
    size: TerminalSize


@dataclass(frozen=True)
class CloseConsole:
    """CLIを終了させない切断要求。"""

    connection_id: ConnectionId


@dataclass(frozen=True)
class ConsoleOpened:
    """Bearerトークンとは別の一時的な接続識別子。"""

    connection_id: ConnectionId


@dataclass(frozen=True)
class ConsoleRead:
    """文字コード境界を保つbase64形式の端末出力。"""

    offset: int
    data: str
    is_closed: bool
    has_pending_turn: bool = False


@dataclass(frozen=True)
class ConsoleAck:
    """操作が処理されたことを示す応答。"""

    is_ok: bool = True


ConsoleCommand = OpenConsole | ReadConsole | WriteConsole | ResizeConsole | CloseConsole
ConsoleResponse = ConsoleOpened | ConsoleRead | ConsoleAck


def parse_console_command(payload: object) -> ConsoleCommand:
    """不正なJSONを端末操作へ持ち込む前に拒否する。"""
    if not isinstance(payload, dict):
        raise ValueError("端末操作はJSONオブジェクトで指定してください")
    action = payload.get("action")
    if action == "open":
        return OpenConsole(_text(payload.get("name")), _text(payload.get("provider")), _size(payload))
    connection_id = ConnectionId(_text(payload.get("connection_id")))
    return _parse_connected_command(payload, action, connection_id)


def _parse_connected_command(payload: dict[str, object], action: object, connection_id: ConnectionId) -> ConsoleCommand:
    """接続済みの操作をそれぞれの型へ変換する。"""
    if action == "read":
        return ReadConsole(connection_id, _integer(payload.get("offset")))
    if action == "input":
        data = base64.b64decode(_text(payload.get("data"), MAX_KEY_BYTES * 2), validate=True)
        if not data or len(data) > MAX_KEY_BYTES:
            raise ValueError("端末入力のサイズが不正です")
        return WriteConsole(connection_id, data)
    if action == "resize":
        return ResizeConsole(connection_id, _size(payload))
    if action != "close":
        raise ValueError("未対応の端末操作です")
    return CloseConsole(connection_id)


def _text(value: object, maximum: int = 200) -> str:
    """接続先や入力データの型・上限を確認する。"""
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError("端末操作の文字列が不正です")
    return value


def _integer(value: object) -> int:
    """boolを寸法やオフセットとして受け入れない。"""
    if type(value) is not int or value < 0:
        raise ValueError("端末操作の整数が不正です")
    return value


def _size(payload: dict[str, object]) -> TerminalSize:
    """サイズの範囲は共通のモデルで検証する。"""
    return TerminalSize(_integer(payload.get("columns")), _integer(payload.get("rows")))
