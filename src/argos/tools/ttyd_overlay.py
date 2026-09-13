"""ttyd と tmux をダッシュボードオーバーレイへ表示する補助ツール。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from argos.yaml_config import load_yaml_environment
from argos.services.dashboard.tls import ensure_self_signed_certificate


DEFAULT_TTYD_HOST = "127.0.0.1"
DEFAULT_TTYD_PORT = 7681
DEFAULT_TMUX_SESSION = "argos-terminal"


@dataclass(frozen=True)
class TtydTlsConfig:
    """ttydがダッシュボードと共有するHTTPS設定。"""

    is_enabled: bool = False
    certificate_path: Path = Path("~/.config/argos/tls/dashboard.crt")
    key_path: Path = Path("~/.config/argos/tls/dashboard.key")


DEFAULT_TTYD_TLS_CONFIG = TtydTlsConfig()


def is_port_open(host: str, port: int) -> bool:
    """指定ポートが接続可能ならTrueを返す。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0


def build_ttyd_command(
    host: str,
    port: int,
    session: str,
    tls: TtydTlsConfig = DEFAULT_TTYD_TLS_CONFIG,
) -> list[str]:
    """ttydでtmuxセッションへ接続するコマンド列を作る。"""
    command = [
        "ttyd",
        "-i",
        host,
        "-p",
        str(port),
    ]
    if tls.is_enabled:
        command.extend(["-S", "-C", str(tls.certificate_path), "-K", str(tls.key_path)])
    return [*command, "tmux", "attach-session", "-t", session]


def build_overlay_payload(
    target_slot: str, title: str, url: str, replace_top: bool
) -> dict[str, object]:
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


def start_ttyd_if_needed(
    host: str,
    port: int,
    session: str,
    tls: TtydTlsConfig = DEFAULT_TTYD_TLS_CONFIG,
) -> bool:
    """ttydが未起動なら起動し、起動した場合だけTrueを返す。"""
    if is_port_open(host, port):
        return False
    if shutil.which("ttyd") is None:
        raise RuntimeError(
            "ttyd が見つかりません。先に ttyd をインストールしてください。"
        )
    if tls.is_enabled:
        ensure_self_signed_certificate(tls.certificate_path, tls.key_path, host)
    subprocess.Popen(
        build_ttyd_command(host, port, session, tls),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return True


def post_overlay_event(
    dashboard_url: str,
    token: str,
    payload: dict[str, object],
    certificate_path: Path | None = None,
) -> dict[str, object]:
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
        context = _dashboard_ssl_context(dashboard_url, certificate_path)
        with urlopen(request, timeout=5, context=context) as response:
            response_data: object = json.loads(response.read().decode("utf-8"))
        if not isinstance(response_data, dict):
            raise RuntimeError(
                "ダッシュボードAPIの応答がJSONオブジェクトではありません"
            )
        return {str(key): value for key, value in response_data.items()}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"ダッシュボードAPIが失敗しました: {exc.code} {detail}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"ダッシュボードAPIへ接続できません: {exc}") from exc


def make_parser() -> argparse.ArgumentParser:
    """コマンドライン引数パーサーを作る。"""
    parser = argparse.ArgumentParser(
        description="ttyd + tmux をARGOSダッシュボードへ表示します"
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        default=Path("config.yaml"),
        help="ARGOS共通設定ファイル",
    )
    parser.add_argument("--dashboard-url", default="", help="ARGOSダッシュボードURL")
    parser.add_argument("--token", default="", help="ARGOS_DASHBOARD_TOKEN")
    parser.add_argument(
        "--target-slot",
        choices=["center", "right"],
        default="center",
        help="表示先スロット",
    )
    parser.add_argument("--title", default="tmux", help="オーバーレイタイトル")
    parser.add_argument(
        "--session", default=DEFAULT_TMUX_SESSION, help="tmuxセッション名"
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="新規tmuxセッションの作業ディレクトリ",
    )
    parser.add_argument("--ttyd-host", default=DEFAULT_TTYD_HOST, help="ttyd bind host")
    parser.add_argument(
        "--ttyd-port", type=int, default=DEFAULT_TTYD_PORT, help="ttyd port"
    )
    parser.add_argument("--public-url", default="", help="iframeへ渡すttyd URL")
    parser.add_argument(
        "--replace-top",
        action="store_true",
        help="既存オーバーレイを積まずに差し替える",
    )
    parser.add_argument(
        "--no-start", action="store_true", help="tmux/ttydを起動せずoverlay送信だけ行う"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """ttyd起動とoverlay表示を実行する。"""
    args = make_parser().parse_args(argv)
    config_values = load_yaml_environment(args.config_file)
    dashboard_url = args.dashboard_url or _dashboard_url(config_values)
    token = (
        args.token
        or os.environ.get("ARGOS_DASHBOARD_TOKEN")
        or config_values.get("ARGOS_DASHBOARD_TOKEN", "")
    )
    tls = _ttyd_tls_config(config_values)
    ttyd_scheme = "https" if tls.is_enabled else "http"
    public_url = (
        args.public_url or f"{ttyd_scheme}://{args.ttyd_host}:{args.ttyd_port}/"
    )

    if not args.no_start:
        ensure_tmux_session(args.session, args.workdir)
        start_ttyd_if_needed(args.ttyd_host, args.ttyd_port, args.session, tls)

    payload = build_overlay_payload(
        args.target_slot, args.title, public_url, args.replace_top
    )
    certificate = Path(
        config_values.get(
            "ARGOS_DASHBOARD_SSL_CERT_PATH", "~/.config/argos/tls/dashboard.crt"
        )
    ).expanduser()
    response = post_overlay_event(dashboard_url, token, payload, certificate)
    print(json.dumps(response, ensure_ascii=False))
    return 0


def _dashboard_url(values: dict[str, str]) -> str:
    """共通設定からローカルのダッシュボードURLを組み立てる。"""
    explicit = values.get("ARGOS_DASHBOARD_URL", "").strip()
    if explicit:
        return explicit
    is_ssl = values.get("ARGOS_DASHBOARD_SSL", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    scheme = "https" if is_ssl else "http"
    port = values.get("ARGOS_DASHBOARD_PORT", "8765")
    return f"{scheme}://127.0.0.1:{port}"


def _ttyd_tls_config(values: dict[str, str]) -> TtydTlsConfig:
    """共通設定からttyd用の証明書設定を作る。"""
    is_enabled = values.get("ARGOS_DASHBOARD_SSL", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    certificate_path = Path(
        values.get("ARGOS_DASHBOARD_SSL_CERT_PATH", "~/.config/argos/tls/dashboard.crt")
    ).expanduser()
    key_path = Path(
        values.get("ARGOS_DASHBOARD_SSL_KEY_PATH", "~/.config/argos/tls/dashboard.key")
    ).expanduser()
    return TtydTlsConfig(is_enabled, certificate_path, key_path)


def _dashboard_ssl_context(
    dashboard_url: str, certificate_path: Path | None
) -> ssl.SSLContext | None:
    """自己署名証明書を信頼するHTTPSコンテキストを必要時だけ作る。"""
    if not dashboard_url.lower().startswith("https://"):
        return None
    if certificate_path is None or not certificate_path.is_file():
        raise RuntimeError(
            f"ダッシュボードTLS証明書が見つかりません: {certificate_path}"
        )
    return ssl.create_default_context(cafile=certificate_path)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
