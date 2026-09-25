"""ダッシュボード用TLS証明書の準備。"""

from __future__ import annotations

import ipaddress
import os
import socket
import subprocess
import uuid
from collections.abc import Sequence
from pathlib import Path
from subprocess import CompletedProcess
from typing import Protocol


CERTIFICATE_VALID_DAYS = 825


class OpenSslRunner(Protocol):
    """opensslコマンドを実行する関数のインターフェース。"""

    def __call__(
        self,
        command: Sequence[str],
        **_options: object,
    ) -> CompletedProcess[str]:
        """指定したコマンドを実行して結果を返す。"""
        ...


def ensure_self_signed_certificate(
    certificate_path: Path,
    key_path: Path,
    host: str,
    *,
    runner: OpenSslRunner | None = None,
) -> None:
    """証明書が未作成なら自己署名証明書と秘密鍵を安全に生成する。"""
    certificate_exists = certificate_path.is_file()
    key_exists = key_path.is_file()
    if certificate_exists and key_exists:
        _restrict_permissions(certificate_path, key_path)
        return
    if certificate_exists != key_exists:
        raise RuntimeError(
            "TLS証明書と秘密鍵の片方だけが存在します。両方を揃えてください"
        )

    _prepare_parent_directories(certificate_path, key_path)
    temporary_certificate = certificate_path.with_name(
        f".{certificate_path.name}.{uuid.uuid4().hex}.tmp"
    )
    temporary_key = key_path.with_name(f".{key_path.name}.{uuid.uuid4().hex}.tmp")
    command = _openssl_command(temporary_certificate, temporary_key, host)
    try:
        result = _execute_openssl(command, runner)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "詳細なし").strip()
            raise RuntimeError(f"自己署名証明書を生成できません: {detail}")
        temporary_key.chmod(0o600)
        temporary_certificate.chmod(0o644)
        os.replace(temporary_key, key_path)
        os.replace(temporary_certificate, certificate_path)
    except FileNotFoundError as exc:
        raise RuntimeError("自己署名証明書の生成にopensslコマンドが必要です") from exc
    finally:
        temporary_certificate.unlink(missing_ok=True)
        temporary_key.unlink(missing_ok=True)
    _restrict_permissions(certificate_path, key_path)


def _execute_openssl(
    command: Sequence[str], runner: OpenSslRunner | None
) -> CompletedProcess[str]:
    """テスト差し替え可能な実行関数でopensslを起動する。"""
    if runner is not None:
        return runner(command, check=False, capture_output=True, text=True, timeout=30)
    return subprocess.run(
        command, check=False, capture_output=True, text=True, timeout=30
    )


def _prepare_parent_directories(certificate_path: Path, key_path: Path) -> None:
    """証明書と秘密鍵の保存先を所有者専用で作成する。"""
    for directory in {certificate_path.parent, key_path.parent}:
        already_exists = directory.exists()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not already_exists:
            directory.chmod(0o700)


def _restrict_permissions(certificate_path: Path, key_path: Path) -> None:
    """秘密鍵を所有者専用、証明書を公開情報として読取可能にする。"""
    key_path.chmod(0o600)
    certificate_path.chmod(0o644)


def _openssl_command(
    certificate_path: Path, key_path: Path, host: str
) -> Sequence[str]:
    """ホスト名とループバックをSANへ含めたopensslコマンドを返す。"""
    common_name = _certificate_common_name(host)
    return (
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:3072",
        "-sha256",
        "-nodes",
        "-days",
        str(CERTIFICATE_VALID_DAYS),
        "-subj",
        f"/CN={common_name}",
        "-addext",
        f"subjectAltName={_subject_alternative_names(host)}",
        "-keyout",
        str(key_path),
        "-out",
        str(certificate_path),
    )


def _certificate_common_name(host: str) -> str:
    """待受アドレスがワイルドカードなら端末名をCNに使う。"""
    normalized = host.strip()
    if normalized in {"", "0.0.0.0", "::"}:
        return socket.gethostname() or "localhost"
    return normalized


def _subject_alternative_names(host: str) -> str:
    """ローカル利用と指定ホストを覆うSAN文字列を作る。"""
    names = ["DNS:localhost", f"DNS:{socket.gethostname()}", "IP:127.0.0.1", "IP:::1"]
    normalized = host.strip()
    if normalized and normalized not in {
        "0.0.0.0",
        "::",
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        try:
            ipaddress.ip_address(normalized)
        except ValueError:
            names.append(f"DNS:{normalized}")
        else:
            names.append(f"IP:{normalized}")
    return ",".join(dict.fromkeys(names))
