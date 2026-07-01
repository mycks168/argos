from pathlib import Path
from io import BytesIO
from urllib.error import HTTPError

import pytest

from argos.tools import ttyd_overlay
from argos.tools.ttyd_overlay import (
    build_overlay_payload,
    build_ttyd_command,
    ensure_tmux_session,
    main,
    parse_env_file,
    post_overlay_event,
    start_ttyd_if_needed,
)


def test_build_ttyd_command():
    """ttydでtmuxへ接続するコマンドを作れる。"""
    assert build_ttyd_command("127.0.0.1", 7681, "argos-terminal") == [
        "ttyd",
        "-i",
        "127.0.0.1",
        "-p",
        "7681",
        "tmux",
        "attach-session",
        "-t",
        "argos-terminal",
    ]


def test_build_overlay_payload():
    """ダッシュボードoverlayイベントのpayloadを作れる。"""
    assert build_overlay_payload("center", "tmux", "http://127.0.0.1:7681/", True) == {
        "type": "overlay",
        "target_slot": "center",
        "overlay_type": "terminal",
        "title": "tmux",
        "url": "http://127.0.0.1:7681/",
        "replace_top": True,
    }


def test_parse_env_file(tmp_path: Path):
    """ARGOS_DASHBOARD_TOKENなどを.envから読める。"""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ARGOS_DASHBOARD_TOKEN='secret-token'\n"
        'ARGOS_DASHBOARD_URL="http://localhost:8765"\n'
        "# comment\n",
        encoding="utf-8",
    )

    assert parse_env_file(env_path) == {
        "ARGOS_DASHBOARD_TOKEN": "secret-token",
        "ARGOS_DASHBOARD_URL": "http://localhost:8765",
    }


def test_ensure_tmux_session_creates_missing_session(monkeypatch, tmp_path: Path):
    """tmuxセッションがない場合は作業ディレクトリ付きで作成する。"""
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return type("Result", (), {"returncode": 1 if "has-session" in command else 0})()

    monkeypatch.setattr(ttyd_overlay.subprocess, "run", fake_run)

    ensure_tmux_session("argos-terminal", tmp_path)

    assert calls[0][0] == ["tmux", "has-session", "-t", "argos-terminal"]
    assert calls[1][0] == ["tmux", "new-session", "-d", "-s", "argos-terminal", "-c", str(tmp_path)]
    assert calls[1][1]["check"] is True


def test_ensure_tmux_session_skips_existing_session(monkeypatch):
    """tmuxセッションがある場合は作成しない。"""
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(ttyd_overlay.subprocess, "run", fake_run)

    ensure_tmux_session("argos-terminal")

    assert calls == [["tmux", "has-session", "-t", "argos-terminal"]]


def test_start_ttyd_if_needed_skips_open_port(monkeypatch):
    """既にttydポートが開いている場合は起動しない。"""
    monkeypatch.setattr(ttyd_overlay, "is_port_open", lambda _host, _port: True)

    assert start_ttyd_if_needed("127.0.0.1", 7681, "argos-terminal") is False


def test_start_ttyd_if_needed_starts_process(monkeypatch):
    """ttydが未起動ならPopenで起動する。"""
    popen_calls = []
    monkeypatch.setattr(ttyd_overlay, "is_port_open", lambda _host, _port: False)
    monkeypatch.setattr(ttyd_overlay.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(ttyd_overlay.subprocess, "Popen", lambda *args, **kwargs: popen_calls.append((args, kwargs)))

    assert start_ttyd_if_needed("127.0.0.1", 7681, "argos-terminal") is True
    assert popen_calls[0][0][0] == build_ttyd_command("127.0.0.1", 7681, "argos-terminal")
    assert popen_calls[0][1]["start_new_session"] is True


def test_start_ttyd_if_needed_requires_ttyd(monkeypatch):
    """ttydコマンドがない場合は分かるエラーにする。"""
    monkeypatch.setattr(ttyd_overlay, "is_port_open", lambda _host, _port: False)
    monkeypatch.setattr(ttyd_overlay.shutil, "which", lambda _command: None)

    with pytest.raises(RuntimeError, match="ttyd"):
        start_ttyd_if_needed("127.0.0.1", 7681, "argos-terminal")


def test_post_overlay_event(monkeypatch):
    """Bearer付きでダッシュボードAPIへイベントを送る。"""
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"status":"overlay_updated"}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["auth"] = request.headers["Authorization"]
        captured["timeout"] = timeout
        captured["data"] = request.data
        return FakeResponse()

    monkeypatch.setattr(ttyd_overlay, "urlopen", fake_urlopen)

    response = post_overlay_event(
        "http://127.0.0.1:8765",
        "secret",
        build_overlay_payload("center", "tmux", "http://127.0.0.1:7681/", False),
    )

    assert response == {"status": "overlay_updated"}
    assert captured["url"] == "http://127.0.0.1:8765/api/events"
    assert captured["auth"] == "Bearer secret"
    assert b'"overlay_type": "terminal"' in captured["data"]


def test_post_overlay_event_requires_token():
    """ダッシュボードトークンがない場合は送信しない。"""
    with pytest.raises(RuntimeError, match="ARGOS_DASHBOARD_TOKEN"):
        post_overlay_event("http://127.0.0.1:8765", "", {})


def test_post_overlay_event_reports_http_error(monkeypatch):
    """HTTPエラー本文を含めて例外にする。"""
    error = HTTPError("http://127.0.0.1:8765/api/events", 401, "Unauthorized", {}, BytesIO(b"bad token"))
    monkeypatch.setattr(ttyd_overlay, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))

    with pytest.raises(RuntimeError, match="401 bad token"):
        post_overlay_event("http://127.0.0.1:8765", "secret", {})


def test_main_can_send_overlay_without_starting_processes(monkeypatch, tmp_path: Path, capsys):
    """--no-startならtmux/ttyd起動を省いてoverlayだけ送る。"""
    env_path = tmp_path / ".env"
    env_path.write_text("ARGOS_DASHBOARD_TOKEN=secret\n", encoding="utf-8")
    sent = []
    monkeypatch.delenv("ARGOS_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr(ttyd_overlay, "post_overlay_event", lambda url, token, payload: sent.append((url, token, payload)) or {"ok": True})

    result = main(["--env-file", str(env_path), "--no-start", "--target-slot", "right"])

    assert result == 0
    assert sent[0][0] == "http://127.0.0.1:8765"
    assert sent[0][1] == "secret"
    assert sent[0][2]["target_slot"] == "right"
    assert '"ok": true' in capsys.readouterr().out
