"""tmuxセッションを介してTUIアプリを操作するための共通ヘルパー。"""

import subprocess
import time
from collections.abc import Callable


def tmux(*args: str) -> subprocess.CompletedProcess[str]:
    """tmuxコマンドを実行し、結果を返す。"""
    return subprocess.run(["tmux", *args], capture_output=True, text=True, check=True)


def capture(session: str) -> str:
    """セッションのペイン内容をプレーンテキストで取得する。"""
    return tmux("capture-pane", "-t", session, "-p").stdout


def send_keys(session: str, *keys: str) -> None:
    """セッションにキー入力を送る。"""
    tmux("send-keys", "-t", session, *keys)


def session_exists(session: str) -> bool:
    """セッションが存在するかを確認する。"""
    result = subprocess.run(
        ["tmux", "has-session", "-t", session], capture_output=True
    )
    return result.returncode == 0


def wait_for(
    session: str,
    predicate: Callable[[str], bool],
    timeout: float = 30,
    interval: float = 0.5,
) -> str:
    """ペイン内容がpredicateを満たすまで待ち、満たした時点の内容を返す。"""
    deadline = time.monotonic() + timeout
    while True:
        if not session_exists(session):
            raise RuntimeError(f"tmuxセッション({session})が予期せず終了しました")
        text = capture(session)
        if predicate(text):
            return text
        if time.monotonic() > deadline:
            raise TimeoutError(f"タイムアウトしました(session={session})")
        time.sleep(interval)


def cleanup(session: str) -> None:
    """ESC -> /exit で正常終了させ、残っていればkillする。"""
    if not session_exists(session):
        return
    try:
        send_keys(session, "Escape")
        time.sleep(0.3)
        send_keys(session, "/exit", "Enter")
        deadline = time.monotonic() + 5
        while session_exists(session) and time.monotonic() < deadline:
            time.sleep(0.3)
    finally:
        if session_exists(session):
            tmux("kill-session", "-t", session)
