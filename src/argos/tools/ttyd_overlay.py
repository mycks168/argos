"""ttyd と tmux をダッシュボードオーバーレイへ表示する補助ツール。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_DASHBOARD_URL = "http://127.0.0.1:8765"
DEFAULT_TTYD_HOST = "127.0.0.1"
DEFAULT_TTYD_PORT = 7681
DEFAULT_TMUX_SESSION = "argos-terminal"


def parse_env_file(path: Path) -> dict[str, str]:
    """単純な KEY=VALUE 形式の.envを読み込む。"""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def is_port_open(host: str, port: int) -> bool:
    """指定ポートが接続可能ならTrueを返す。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0


def build_ttyd_command(host: str, port: int, session: str) -> list[str]:
    """ttydでtmuxセッションへ接続するコマンド列を作る。"""
    return [
        "ttyd",
        "-i",
        host,
        "-p",
        str(port),
        "tmux",
        "attach-session",
        "-t",
        session,
    ]


def build_overlay_payload(target_slot: str, title: str, url: str, replace_top: bool) -> dict[str, object]:
    """ダッシュボードのoverlayイベントpayloadを作る。"""
    return {
        "type": "overlay",
        "target_slot": target_slot,
        "overlay_type": "terminal",
        "title": title,
        "url": url,
        "replace_top": replace_top,
    }


def ensure_tmux_session(session: str, workdir: Path | None = None) -> None:
    """tmuxセッションがなければ作成する。"""
    has_session = subprocess.run(
        ["tmux", "has-session", "-t", session],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if has_session.returncode == 0:
        return
    command = ["tmux", "new-session", "-d", "-s", session]
    if workdir is not None:
        command.extend(["-c", str(workdir)])
    subprocess.run(command, check=True)


def start_ttyd_if_needed(host: str, port: int, session: str) -> bool:
    """ttydが未起動なら起動し、起動した場合だけTrueを返す。"""
    if is_port_open(host, port):
        return False
    if shutil.which("ttyd") is None:
        raise RuntimeError("ttyd が見つかりません。先に ttyd をインストールしてください。")
    subprocess.Popen(
        build_ttyd_command(host, port, session),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return True


def post_overlay_event(dashboard_url: str, token: str, payload: dict[str, object]) -> dict[str, object]:
    """ARGOSダッシュボードへoverlayイベントを送信する。"""
    if not token:
        raise RuntimeError("ARGOS_DASHBOARD_TOKEN が未設定です。")
    request = Request(
        f"{dashboard_url.rstrip('/')}/api/events",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ダッシュボードAPIが失敗しました: {exc.code} {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"ダッシュボードAPIへ接続できません: {exc}") from exc


def make_parser() -> argparse.ArgumentParser:
    """コマンドライン引数パーサーを作る。"""
    parser = argparse.ArgumentParser(description="ttyd + tmux をARGOSダッシュボードへ表示します")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help=".envファイル")
    parser.add_argument("--dashboard-url", default="", help="ARGOSダッシュボードURL")
    parser.add_argument("--token", default="", help="ARGOS_DASHBOARD_TOKEN")
    parser.add_argument("--target-slot", choices=["center", "right"], default="center", help="表示先スロット")
    parser.add_argument("--title", default="tmux", help="オーバーレイタイトル")
    parser.add_argument("--session", default=DEFAULT_TMUX_SESSION, help="tmuxセッション名")
    parser.add_argument("--workdir", type=Path, default=None, help="新規tmuxセッションの作業ディレクトリ")
    parser.add_argument("--ttyd-host", default=DEFAULT_TTYD_HOST, help="ttyd bind host")
    parser.add_argument("--ttyd-port", type=int, default=DEFAULT_TTYD_PORT, help="ttyd port")
    parser.add_argument("--public-url", default="", help="iframeへ渡すttyd URL")
    parser.add_argument("--replace-top", action="store_true", help="既存オーバーレイを積まずに差し替える")
    parser.add_argument("--no-start", action="store_true", help="tmux/ttydを起動せずoverlay送信だけ行う")
    return parser


def main(argv: list[str] | None = None) -> int:
    """ttyd起動とoverlay表示を実行する。"""
    args = make_parser().parse_args(argv)
    env_values = parse_env_file(args.env_file)
    dashboard_url = args.dashboard_url or env_values.get("ARGOS_DASHBOARD_URL") or DEFAULT_DASHBOARD_URL
    token = args.token or os.environ.get("ARGOS_DASHBOARD_TOKEN") or env_values.get("ARGOS_DASHBOARD_TOKEN", "")
    public_url = args.public_url or f"http://{args.ttyd_host}:{args.ttyd_port}/"

    if not args.no_start:
        ensure_tmux_session(args.session, args.workdir)
        start_ttyd_if_needed(args.ttyd_host, args.ttyd_port, args.session)

    payload = build_overlay_payload(args.target_slot, args.title, public_url, args.replace_top)
    response = post_overlay_event(dashboard_url, token, payload)
    print(json.dumps(response, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
