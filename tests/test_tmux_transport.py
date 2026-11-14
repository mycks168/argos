"""tmux制御でターゲットと本文を取り違えないことを検証する。"""

import subprocess
from unittest.mock import patch

import pytest

from argos.services.agent.tmux_transport import (
    TerminalSize,
    TmuxSlotIdentity,
    TmuxTransport,
)
from argos.services.agent.tmux_input import MAX_INPUT_BYTES


def make_identity(generation: str = "test") -> TmuxSlotIdentity:
    """シェル構文を含む名前でも制御引数へ漏れない試験値を返す。"""
    return TmuxSlotIdentity("作業; $(false)", "claude", "/tmp/作業", generation)


def test_session_identity() -> None:
    """同じスロットは安定し、世代を変えたスロットは分離する。"""
    identity = make_identity()
    assert identity.session_name == make_identity().session_name
    assert identity.session_name != make_identity("other").session_name
    assert identity.session_name.startswith("argos_slot_")
    assert len(identity.session_name) == len("argos_slot_") + 64


@pytest.mark.parametrize("dimension", [0, 1, 1001, True, 3.5])
def test_invalid_dimensions(dimension: int) -> None:
    """不正な端末サイズを外部プロセス呼び出し前に拒否する。"""
    with pytest.raises(ValueError, match="端末"):
        TerminalSize(dimension, 24)


def test_resize_window() -> None:
    """存在しないclientではなく所有windowのサイズを変更する。"""
    identity = make_identity()
    with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as command:
        TmuxTransport(identity).resize(TerminalSize(120, 40))
    assert command.call_args.args[0] == [
        "tmux", "resize-window", "-t", f"={identity.session_name}:0",
        "-x", "120", "-y", "40",
    ]
    assert command.call_args.kwargs["timeout"] == 5


def test_paste_literal_multiline() -> None:
    """本文の改行やコマンド風文字列をシェル引数へ入れない。"""
    identity = make_identity()
    text = "--help\n$(false)\n日本語"
    with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as command:
        TmuxTransport(identity).send_text(text)
    calls = command.call_args_list
    assert calls[0].kwargs["input"] == text.encode("utf-8")
    assert calls[1].args[0] == [
        "tmux", "paste-buffer", "-p", "-b", identity.session_name,
        "-t", f"={identity.session_name}:0.0",
    ]
    assert calls[2].args[0] == ["tmux", "delete-buffer", "-b", identity.session_name]
    assert all(text not in call.args[0] for call in calls)


@pytest.mark.parametrize("text", ["", " \n", "a\0b", "a" * (MAX_INPUT_BYTES + 1)])
def test_invalid_input(text: str) -> None:
    """不正な入力ではtmuxへアクセスしない。"""
    with patch("subprocess.run") as command, pytest.raises(ValueError, match="端末入力"):
        TmuxTransport(make_identity()).send_text(text)
    command.assert_not_called()


@pytest.mark.parametrize("error", [OSError("機微な情報"), subprocess.TimeoutExpired("tmux", 5)])
def test_execution_failure(error: Exception) -> None:
    """実行失敗の説明には操作を示し、生の例外を含めない。"""
    with (
        patch("subprocess.run", side_effect=error),
        pytest.raises(RuntimeError, match="operation=resize-window") as captured,
    ):
        TmuxTransport(make_identity()).resize(TerminalSize(80, 24))
    assert "機微な情報" not in str(captured.value)


def test_failed_paste_removes_buffer() -> None:
    """貼り付けが失敗しても本文のバッファを消す。"""
    outcomes: list[subprocess.CompletedProcess[bytes]] = [
        subprocess.CompletedProcess([], status) for status in (0, 1, 0)
    ]
    with (
        patch("subprocess.run", side_effect=outcomes) as command,
        pytest.raises(RuntimeError, match="operation=paste-buffer"),
    ):
        TmuxTransport(make_identity()).send_text("本文")
    assert command.call_args.args[0][1] == "delete-buffer"


def test_explicit_pane_and_submit() -> None:
    """base-index設定に依存せず、保存したpaneへ送信する。"""
    with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as command:
        TmuxTransport(make_identity(), "%27").submit()
    assert command.call_args.args[0] == ["tmux", "send-keys", "-t", "%27", "Enter"]
    with pytest.raises(ValueError, match="pane ID"):
        TmuxTransport(make_identity(), "任意のターゲット")
