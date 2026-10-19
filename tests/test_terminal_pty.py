"""実PTYでサイズ、再読込、切断時のプロセス回収を確認する。"""

import os
import subprocess
from unittest.mock import Mock

import pytest

from argos.services.agent.terminal_pty import TerminalPty
from argos.services.agent.tmux_transport import TerminalSize


def test_pty_roundtrip_and_resize(monkeypatch: pytest.MonkeyPatch) -> None:
    """端末から見える列数が80固定にならず、再読込で文字化けしない。"""
    monkeypatch.setenv("TMUX", "invalid-nested-session")
    terminal = TerminalPty(("/bin/sh", "-c", "stty size; exec cat"), TerminalSize(123, 37))
    try:
        first = terminal.read(0)
        assert b"37 123" in first.data
        assert terminal.read(0).data == first.data
        terminal.resize(TerminalSize(95, 28))
        terminal.write("日本語\n".encode())
        assert "日本語".encode() in terminal.read(first.offset).data
        assert not terminal.is_closed
        with pytest.raises(ValueError, match="読込位置"):
            terminal.read(-1)
    finally:
        terminal.close()
    assert terminal.is_closed
    with pytest.raises(RuntimeError):
        terminal.resize(TerminalSize(80, 24))
    with pytest.raises(RuntimeError):
        terminal.write(b"x")


def test_pty_buffer_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """遅延したブラウザへ出力の欠落を黙って隠さない。"""
    monkeypatch.setattr("argos.services.agent.terminal_pty.MAX_BUFFER_BYTES", 8)
    terminal = TerminalPty(("/bin/cat",), TerminalSize(80, 24))
    try:
        terminal._append(b"abcdefghijkl")
        with pytest.raises(ValueError):
            terminal.read(0)
        assert terminal.read(4).data == b"efghijkl"
    finally:
        terminal.close()


def test_pty_launch_error() -> None:
    """起動に失敗しても両側のファイル記述子を閉じる。"""
    with pytest.raises(FileNotFoundError):
        TerminalPty(("/nonexistent-argos-test",), TerminalSize(80, 24))


def test_pty_eof_and_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    """終了と放置の両方でattachだけを回収する。"""
    terminal = TerminalPty(("/bin/true",), TerminalSize(80, 24))
    terminal._thread.join(timeout=3)
    assert terminal.read(0).is_closed
    terminal.close()
    monkeypatch.setattr("argos.services.agent.terminal_pty.IDLE_TIMEOUT_SECONDS", 0)
    terminal = TerminalPty(("/bin/cat",), TerminalSize(80, 24))
    terminal._thread.join(timeout=3)
    assert terminal.is_closed


def test_pty_termination_escalation() -> None:
    """終了を無視するattachも自身の子プロセスに限って強制回収する。"""
    terminal = object.__new__(TerminalPty)
    process = Mock(wait=Mock(side_effect=[subprocess.TimeoutExpired("attach", 1), 0]))
    process.poll.return_value = None
    terminal._process = process
    terminal._terminate_client()
    process.kill.assert_called_once()
    terminal._process = Mock(poll=Mock(return_value=None), terminate=Mock(side_effect=ProcessLookupError))
    terminal._terminate_client()


def test_pty_zero_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """EIOではなくEOFを返す環境でも正常終了する。"""
    terminal = TerminalPty(("/bin/cat",), TerminalSize(80, 24))
    terminal.close()
    terminal._stop.clear()
    terminal._master, slave = os.openpty()
    monkeypatch.setattr("argos.services.agent.terminal_pty.select.select", lambda *_args: ([terminal._master], [], []))
    monkeypatch.setattr("argos.services.agent.terminal_pty.os.read", lambda *_args: b"")
    try:
        terminal._pump()
    finally:
        os.close(slave)
