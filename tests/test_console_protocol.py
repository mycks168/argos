"""端末操作の境界値と会話入力の区別を検証する。"""

import base64

import pytest

from argos.services.agent.console_protocol import (
    CloseConsole, OpenConsole, ReadConsole, ResizeConsole, WriteConsole,
    ObservedTerminalInput, parse_console_command, split_terminal_input,
)


@pytest.mark.parametrize("action, expected", [("read", ReadConsole), ("resize", ResizeConsole), ("close", CloseConsole), ("input", WriteConsole)])
def test_connected_commands(action: str, expected: type) -> None:
    """同じ接続識別子でも操作ごとのモデルを返す。"""
    command = parse_console_command({"action": action, "connection_id": "test", "offset": 0,
                                     "columns": 120, "rows": 35, "data": base64.b64encode("日本語".encode()).decode()})
    assert isinstance(command, expected)


def test_open_and_split() -> None:
    """送信済みの回答取得元を通常入力と混同しない。"""
    assert isinstance(parse_console_command({"action": "open", "name": "試験", "provider": "claude", "columns": 80, "rows": 24}), OpenConsole)
    source = ObservedTerminalInput("端末", ["回答"])
    assert split_terminal_input(source) == ("端末", ["回答"])
    assert split_terminal_input("通常") == ("通常", None)
    assert split_terminal_input(None) == (None, None)


@pytest.mark.parametrize("payload", [None, [], {}, {"action": "other", "connection_id": "test"},
    {"action": "read", "connection_id": "test", "offset": True},
    {"action": "read", "connection_id": "test", "offset": -1},
    {"action": "input", "connection_id": "test", "data": "%%%"},
    {"action": "input", "connection_id": "test", "data": "A" * 90000},
    {"action": "close", "connection_id": "t" * 201},
    {"action": "open", "name": "test", "provider": "claude", "columns": 1, "rows": 24}])
def test_invalid_commands(payload: object) -> None:
    """不正値はプロセス操作より前に拒否する。"""
    with pytest.raises(ValueError):
        parse_console_command(payload)
