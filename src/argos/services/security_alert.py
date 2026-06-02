"""本人確認失敗時の外部アクションを実行する。"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class SecurityAlertResult:
    """警戒アクションの実行結果。"""

    executed: bool
    succeeded: bool
    message: str


class SecurityAlertDispatcher:
    """設定されたコマンドで警戒通知を外部へ送る。"""

    def __init__(self, command: str) -> None:
        """外部通知コマンドを保持する。"""
        self._command = command.strip()

    def dispatch(self, source: str, message: str, image_path: str = "") -> SecurityAlertResult:
        """警戒通知コマンドを実行する。"""
        if not self._command:
            return SecurityAlertResult(False, True, "警戒通知コマンドは未設定です。")
        command = self._command.format(source=source, message=message, image_path=image_path)
        try:
            subprocess.run(command, shell=True, check=True, timeout=10)
        except subprocess.CalledProcessError as exc:
            return SecurityAlertResult(True, False, f"警戒通知コマンドが失敗しました。終了コード={exc.returncode}")
        except subprocess.TimeoutExpired:
            return SecurityAlertResult(True, False, "警戒通知コマンドがタイムアウトしました。")
        return SecurityAlertResult(True, True, "警戒通知を送信しました。")
