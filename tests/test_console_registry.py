"""ローカル・リモートの端末登録簿とHTTP認証を検証する。"""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import requests

from argos.config import AgentSlot, load_settings
from argos.services.agent.console_protocol import (
    CloseConsole, ConnectionId, ConsoleAck, ConsoleCommand, ConsoleOpened, ConsoleRead, ConsoleResponse,
    OpenConsole, ReadConsole, ResizeConsole, WriteConsole,
)
from argos.services.agent.console_registry import ConsoleRegistry
from argos.services.agent.remote_console import RemoteConsole, remote_execution_mode
from argos.services.agent.terminal_pty import TerminalChunk
from argos.services.agent.tmux_transport import TerminalSize
from argos.services.dashboard.server import DashboardServer
from argos.services.dashboard.state import DashboardState


@pytest.fixture
def registry(tmp_path: Path) -> ConsoleRegistry:
    """実利用の台帳やセッションに触れない設定を返す。"""
    slot = AgentSlot("試験", "claude", str(tmp_path), execution_mode="tmux")
    settings = replace(load_settings(), agent_slots=(slot,), agent_state_path=str(tmp_path / "sessions.json"))
    return ConsoleRegistry(settings)


def test_registry_local(registry: ConsoleRegistry, monkeypatch: pytest.MonkeyPatch) -> None:
    """所有するClaudeだけへ接続し、切断はattachに限定する。"""
    terminal, client = Mock(), Mock()
    terminal.is_closed = False
    terminal.read.return_value = TerminalChunk(b"abc", 3, False)
    client.observe_stream.return_value = iter(["回答"])
    client.has_pending_manual_turn.return_value = False
    monkeypatch.setattr("argos.services.agent.console_registry.ClaudeTmuxClient", Mock(return_value=client))
    monkeypatch.setattr("argos.services.agent.console_registry.TerminalPty", Mock(return_value=terminal))
    monkeypatch.setattr("argos.services.agent.console_registry.run_tmux", Mock())
    opened = registry.execute(OpenConsole("試験", "claude", TerminalSize(90, 30)))
    assert isinstance(opened, ConsoleOpened)
    assert ConsoleAck().is_ok
    assert not registry._connections[opened.connection_id].is_closed
    assert registry.execute(ReadConsole(opened.connection_id, 0)) == ConsoleRead(3, "YWJj", False)
    registry.execute(WriteConsole(opened.connection_id, b"x"))
    client.console_input.call_args.args[1]()
    terminal.write.assert_called_once_with(b"x")
    registry.execute(ResizeConsole(opened.connection_id, TerminalSize(120, 40)))
    assert list(registry.observe("試験", "claude")) == ["回答"]
    assert registry.execute(CloseConsole(opened.connection_id)) == ConsoleAck()
    terminal.close.assert_called_once()
    with pytest.raises(KeyError):
        registry.execute(ReadConsole(opened.connection_id, 0))
    registry.close()


def test_registry_limits_and_modes(registry: ConsoleRegistry, monkeypatch: pytest.MonkeyPatch) -> None:
    """未定義・従来方式・接続上限を明示的に拒否する。"""
    with pytest.raises(ValueError):
        registry.execution_mode("なし", "claude")
    original = registry._settings
    registry._settings = replace(original, agent_slots=(replace(original.agent_slots[0], execution_mode="cli"),))
    with pytest.raises(ValueError):
        registry.execute(OpenConsole("試験", "claude", TerminalSize(80, 24)))
    with pytest.raises(ValueError):
        list(registry.observe("試験", "claude"))
    monkeypatch.setattr("argos.services.agent.console_registry.MAX_CONNECTIONS", 0)
    with pytest.raises(RuntimeError, match="上限"):
        registry.execute(OpenConsole("試験", "claude", TerminalSize(80, 24)))


def _remote_slot() -> AgentSlot:
    """接続先認証がブラウザへ漏れないことを確認するための設定。"""
    return AgentSlot("遠隔", "remote", "", slot_type="remote", remote_url="http://remote",
                     remote_token="server-only-token", remote_name="先", remote_provider="claude")


def test_remote_registry(registry: ConsoleRegistry, monkeypatch: pytest.MonkeyPatch) -> None:
    """能力照会はキャッシュし、旧版にはtmuxを強制しない。"""
    registry._settings = replace(registry._settings, agent_slots=(_remote_slot(),))
    lookup = Mock(return_value="tmux")
    connection = Mock(is_closed=False)
    monkeypatch.setattr("argos.services.agent.console_registry.remote_execution_mode", lookup)
    monkeypatch.setattr("argos.services.agent.console_registry.RemoteConsole", Mock(return_value=connection))
    client = Mock()
    client.observe_stream.return_value = iter(["遠隔回答"])
    monkeypatch.setattr("argos.services.agent.console_registry.RemoteArgosClient", Mock(return_value=client))
    assert registry.execution_mode("遠隔", "remote") == "tmux"
    assert registry.execution_mode("遠隔", "remote") == "tmux"
    lookup.assert_called_once()
    registry.execute(OpenConsole("遠隔", "remote", TerminalSize(80, 24)))
    assert list(registry.observe("遠隔", "remote")) == ["遠隔回答"]
    registry.close()
    connection.close.assert_called_once()


def test_remote_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """全操作に接続先Bearerと期限を設定し、base64を壊さず中継する。"""
    response = Mock()
    response.json.return_value = {"connection_id": "remote-id"}
    post = Mock(return_value=response)
    monkeypatch.setattr("argos.services.agent.remote_console.requests.post", post)
    console = RemoteConsole(_remote_slot(), TerminalSize(110, 30))
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer server-only-token"
    console.write("日本語".encode())
    console.resize(TerminalSize(90, 24))
    response.json.return_value = {"offset": 3, "data": "YWJj", "is_closed": True}
    monkeypatch.setattr("argos.services.agent.remote_console.requests.get", Mock(return_value=response))
    assert console.read(0) == ConsoleRead(3, "YWJj", True)
    assert console.is_closed
    response.status_code = 404
    with pytest.raises(RuntimeError, match="失効"):
        console.read(0)
    response.status_code = 200
    response.json.return_value = {"offset": True, "data": "", "is_closed": False}
    with pytest.raises(RuntimeError, match="端末出力"):
        console.read(0)
    response.json.return_value = []
    with pytest.raises(RuntimeError):
        console.resize(TerminalSize(80, 24))
    response.json.return_value = {}
    with pytest.raises(RuntimeError, match="端末応答"):
        RemoteConsole(_remote_slot(), TerminalSize(80, 24))
    monkeypatch.setattr("argos.services.agent.remote_console.requests.post", Mock(side_effect=requests.ConnectionError()))
    console.close()


def test_remote_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    """遠隔接続もブラウザ消失後に登録簿の上限を占有し続けない。"""
    response = Mock()
    response.json.return_value = {"connection_id": "remote-id"}
    monkeypatch.setattr("argos.services.agent.remote_console.requests.post", Mock(return_value=response))
    monkeypatch.setattr("argos.services.agent.remote_console.time.monotonic", Mock(return_value=0))
    console = RemoteConsole(_remote_slot(), TerminalSize(80, 24))
    assert not console.is_closed
    monkeypatch.setattr("argos.services.agent.remote_console.time.monotonic", Mock(return_value=100))
    assert console.is_closed


def test_remote_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧版・通信失敗の能力照会ではcliへ戻す。"""
    response = Mock()
    getter = Mock(return_value=response)
    monkeypatch.setattr("argos.services.agent.remote_console.requests.get", getter)
    response.json.return_value = {"slots": [{"name": "先", "provider": "claude", "execution_mode": "tmux"}]}
    assert remote_execution_mode(_remote_slot()) == "tmux"
    response.json.return_value = {}
    assert remote_execution_mode(_remote_slot()) == "cli"
    monkeypatch.setattr("argos.services.agent.remote_console.requests.get", Mock(side_effect=requests.ConnectionError()))
    assert remote_execution_mode(_remote_slot()) == "cli"


class ConsoleHandler:
    """HTTP層の認証とエラー分類を確認する最小の端末ハンドラ。"""

    def __init__(self) -> None:
        """受信操作と次の例外を保持する。"""
        self.command: ConsoleCommand | None = None
        self.error: Exception | None = None
        self.is_closed = False

    def console_request(self, command: ConsoleCommand) -> ConsoleResponse:
        """HTTPから型付きモデルだけが渡されることを確認する。"""
        if self.error:
            raise self.error
        self.command = command
        if isinstance(command, OpenConsole):
            return ConsoleOpened(ConnectionId("attached"))
        if isinstance(command, ReadConsole):
            return ConsoleRead(3, "YWJj", False)
        return ConsoleAck()

    def close_console(self) -> None:
        """終了時の接続回収を確認する。"""
        self.is_closed = True

    def latest_command(self) -> ConsoleCommand | None:
        """別HTTPスレッドで更新された操作の最新値を返す。"""
        return self.command


def test_console_http() -> None:
    """Bearer・Origin・JSON検証とエラーのHTTP対応を実サーバーで確認する。"""
    handler = ConsoleHandler()
    server = DashboardServer(DashboardState(), "127.0.0.1", 0, "secret", terminal_handler=handler)
    server.start()
    url = f"http://127.0.0.1:{server.address[1]}/api/terminal/console"
    payload = {"action": "open", "name": "試験", "provider": "claude", "columns": 80, "rows": 24}
    request = Request(url, data=json.dumps(payload).encode(), headers={"Authorization": "Bearer secret"}, method="POST")
    try:
        with urlopen(request, timeout=3) as response:
            assert json.load(response) == {"connection_id": "attached"}
        for token, origin, status in [("", "", 401), ("Bearer secret", "http://other", 403)]:
            request.headers.update({"Authorization": token, "Origin": origin})
            with pytest.raises(HTTPError) as error:
                urlopen(request, timeout=3)
            assert error.value.code == status
        request.headers.update({"Authorization": "Bearer secret", "Origin": ""})
        for failure, status in [(KeyError("missing"), 404), (ValueError("bad"), 400), (RuntimeError("busy"), 409), (OSError("io"), 502)]:
            handler.error = failure
            with pytest.raises(HTTPError) as error:
                urlopen(request, timeout=3)
            assert error.value.code == status
        handler.error = None
        with urlopen(Request(url + "?connection_id=attached&offset=0", headers={"Authorization": "Bearer secret"}), timeout=3):
            assert isinstance(handler.command, ReadConsole)
    finally:
        server.stop()
    assert handler.is_closed


def test_remote_console_loopback() -> None:
    """モックHTTPではなく接続先ARGOSの同じAPIで全端末操作を往復する。"""
    handler = ConsoleHandler()
    server = DashboardServer(DashboardState(), "127.0.0.1", 0, "server-only-token", terminal_handler=handler)
    server.start()
    slot = replace(_remote_slot(), remote_url=f"http://127.0.0.1:{server.address[1]}")
    try:
        console = RemoteConsole(slot, TerminalSize(90, 24))
        assert handler.latest_command() == OpenConsole("先", "claude", TerminalSize(90, 24))
        assert console.read(0) == ConsoleRead(3, "YWJj", False)
        console.write("日本語".encode())
        assert handler.latest_command() == WriteConsole(ConnectionId("attached"), "日本語".encode())
        console.resize(TerminalSize(120, 35))
        assert handler.latest_command() == ResizeConsole(ConnectionId("attached"), TerminalSize(120, 35))
        console.close()
        assert handler.latest_command() == CloseConsole(ConnectionId("attached"))
    finally:
        server.stop()
