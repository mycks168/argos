"""ダッシュボードHTTPSと自己署名証明書のテスト。"""

from __future__ import annotations

import ssl
import urllib.request
from collections.abc import Sequence
from pathlib import Path

import pytest

from argos.services.dashboard.server import DashboardServer, DashboardTlsConfig
from argos.services.dashboard.state import DashboardState
from argos.services.dashboard.tls import ensure_self_signed_certificate


class _CommandResult:
    """openssl実行結果のテスト用表現。"""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        """終了コードと標準エラーを保持する。"""
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


def _write_fake_certificate(
    command: Sequence[str], **_kwargs: object
) -> _CommandResult:
    """opensslの指定先へテスト用ファイルを生成する。"""
    command_tuple = tuple(command)
    Path(command_tuple[command_tuple.index("-out") + 1]).write_text(
        "certificate", encoding="utf-8"
    )
    Path(command_tuple[command_tuple.index("-keyout") + 1]).write_text(
        "key", encoding="utf-8"
    )
    return _CommandResult()


def test_self_signed_certificate_builds_san_and_permissions(tmp_path: Path) -> None:
    """自動生成はSANを指定し、秘密鍵を所有者専用にする。"""
    certificate_path = tmp_path / "tls" / "dashboard.crt"
    key_path = tmp_path / "tls" / "dashboard.key"
    commands: list[tuple[str, ...]] = []

    def fake_runner(command: Sequence[str], **_kwargs: object) -> _CommandResult:
        """opensslの出力ファイルだけを模擬生成する。"""
        command_tuple = tuple(command)
        commands.append(command_tuple)
        return _write_fake_certificate(command)

    ensure_self_signed_certificate(
        certificate_path,
        key_path,
        "dashboard.example.local",
        runner=fake_runner,
    )

    assert certificate_path.stat().st_mode & 0o777 == 0o644
    assert key_path.stat().st_mode & 0o777 == 0o600
    assert tmp_path.joinpath("tls").stat().st_mode & 0o777 == 0o700
    san = commands[0][commands[0].index("-addext") + 1]
    assert "DNS:localhost" in san
    assert "DNS:dashboard.example.local" in san


def test_self_signed_certificate_reuses_existing_pair(tmp_path: Path) -> None:
    """既存の証明書と秘密鍵は再生成せず再利用する。"""
    certificate_path = tmp_path / "dashboard.crt"
    key_path = tmp_path / "dashboard.key"
    certificate_path.write_text("certificate", encoding="utf-8")
    key_path.write_text("key", encoding="utf-8")

    ensure_self_signed_certificate(
        certificate_path,
        key_path,
        "127.0.0.1",
        runner=lambda *_args, **_kwargs: pytest.fail("opensslを実行してはいけません"),
    )

    assert key_path.stat().st_mode & 0o777 == 0o600


def test_self_signed_certificate_keeps_existing_directory_permissions(
    tmp_path: Path,
) -> None:
    """利用者指定の既存共有ディレクトリの権限は変更しない。"""
    shared_directory = tmp_path / "shared"
    shared_directory.mkdir(mode=0o755)
    certificate_path = shared_directory / "dashboard.crt"
    key_path = shared_directory / "dashboard.key"

    ensure_self_signed_certificate(
        certificate_path,
        key_path,
        "localhost",
        runner=_write_fake_certificate,
    )

    assert shared_directory.stat().st_mode & 0o777 == 0o755


def test_self_signed_certificate_rejects_incomplete_pair(tmp_path: Path) -> None:
    """証明書と秘密鍵の片方だけを誤って上書きしない。"""
    certificate_path = tmp_path / "dashboard.crt"
    certificate_path.write_text("certificate", encoding="utf-8")

    with pytest.raises(RuntimeError, match="片方だけ"):
        ensure_self_signed_certificate(
            certificate_path, tmp_path / "dashboard.key", "localhost"
        )


def test_self_signed_certificate_reports_openssl_failure(tmp_path: Path) -> None:
    """openssl失敗時は詳細を含むエラーにする。"""
    with pytest.raises(RuntimeError, match="生成できません: failed"):
        ensure_self_signed_certificate(
            tmp_path / "dashboard.crt",
            tmp_path / "dashboard.key",
            "localhost",
            runner=lambda *_args, **_kwargs: _CommandResult(1, "failed"),
        )


def test_self_signed_certificate_reports_missing_openssl(tmp_path: Path) -> None:
    """openssl未導入時は必要なコマンドを明示する。"""

    def missing_runner(*_args: object, **_kwargs: object) -> _CommandResult:
        """openssl未導入を模擬する。"""
        raise FileNotFoundError("openssl")

    with pytest.raises(RuntimeError, match="opensslコマンドが必要"):
        ensure_self_signed_certificate(
            tmp_path / "dashboard.crt",
            tmp_path / "dashboard.key",
            "localhost",
            runner=missing_runner,
        )


@pytest.mark.parametrize(
    ("host", "expected_san"),
    (("192.0.2.1", "IP:192.0.2.1"), ("0.0.0.0", "DNS:localhost")),
)
def test_self_signed_certificate_normalizes_host_san(
    tmp_path: Path,
    host: str,
    expected_san: str,
) -> None:
    """IPアドレスとワイルドカード待受を証明書名へ安全に変換する。"""
    commands: list[tuple[str, ...]] = []

    def fake_runner(command: Sequence[str], **_kwargs: object) -> _CommandResult:
        """生成コマンドを記録する。"""
        commands.append(tuple(command))
        return _write_fake_certificate(command)

    ensure_self_signed_certificate(
        tmp_path / f"{host}.crt",
        tmp_path / f"{host}.key",
        host,
        runner=fake_runner,
    )

    san = commands[0][commands[0].index("-addext") + 1]
    assert expected_san in san


def test_dashboard_serves_health_over_https(tmp_path: Path) -> None:
    """HTTPS有効時はTLS経由でダッシュボードへ接続できる。"""
    server = DashboardServer(
        DashboardState(),
        "127.0.0.1",
        0,
        "secret",
        tls=DashboardTlsConfig(
            is_enabled=True,
            certificate_path=tmp_path / "dashboard.crt",
            key_path=tmp_path / "dashboard.key",
        ),
    )
    server.start()
    try:
        host, port = server.address
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(
            f"https://{host}:{port}/api/health", context=context, timeout=2
        ) as response:
            assert response.status == 200
    finally:
        server.stop()
