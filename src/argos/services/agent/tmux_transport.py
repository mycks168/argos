"""所有するtmuxセッションだけを操作する端末制御。"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass

from argos.services.agent.tmux_input import encode_terminal_text


COMMAND_TIMEOUT_SECONDS = 5
MIN_TERMINAL_SIZE = 2
MAX_TERMINAL_SIZE = 1000


@dataclass(frozen=True)
class TmuxSlotIdentity:
    """スロットと実行世代を混同しないための識別情報。"""

    name: str
    provider: str
    cwd: str
    generation: str

    @property
    def session_name(self) -> str:
        """外部入力をtmuxのターゲット構文から隔離する。"""
        identity = "\0".join((self.name, self.provider, self.cwd, self.generation))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"argos_slot_{digest}"


@dataclass(frozen=True)
class TerminalSize:
    """ブラウザが計測した有効範囲内の端末寸法。"""

    columns: int
    rows: int

    def __post_init__(self) -> None:
        """異常な寸法によるtmuxへの過大な要求を拒否する。"""
        for dimension in (self.columns, self.rows):
            if type(dimension) is not int or not MIN_TERMINAL_SIZE <= dimension <= MAX_TERMINAL_SIZE:
                raise ValueError("端末の列数・行数は2から1000の整数で指定してください")


class TmuxTransport:
    """単一スロットのtmux制御コマンドを実行する。"""

    def __init__(self, identity: TmuxSlotIdentity, pane_id: str = "") -> None:
        """操作範囲を安定したセッション名に限定する。"""
        self._session_name = identity.session_name
        if pane_id and re.fullmatch(r"%[0-9]+", pane_id) is None:
            raise ValueError("tmuxのpane IDが不正です")
        self._pane_id = pane_id

    def submit(self) -> None:
        """本文の貼り付けとは別の操作として送信する。"""
        self._execute(["send-keys", "-t", self._pane_target, "Enter"])

    def resize(self, size: TerminalSize) -> None:
        """client名ではなく所有するwindowをリサイズする。"""
        self._execute([
            "resize-window", "-t", self._window_target,
            "-x", str(size.columns), "-y", str(size.rows),
        ])

    def send_text(self, text: str) -> None:
        """シェルやtmuxキー名へ解釈させずに発話本文を貼り付ける。"""
        encoded = encode_terminal_text(text)
        buffer_name = self._session_name
        self._execute(["load-buffer", "-b", buffer_name, "-"], encoded)
        try:
            self._execute([
                "paste-buffer", "-p", "-b", buffer_name, "-t", self._pane_target,
            ])
        finally:
            self._execute(["delete-buffer", "-b", buffer_name])

    @property
    def _window_target(self) -> str:
        """他セッションとの前方一致を防ぐ完全指定を返す。"""
        return self._pane_id or f"={self._session_name}:0"

    @property
    def _pane_target(self) -> str:
        """別windowやpaneへ入力を送らない完全指定を返す。"""
        return self._pane_id or f"{self._window_target}.0"

    def _execute(self, arguments: list[str], stdin: bytes | None = None) -> None:
        """入力本文をエラーに含めず、制御の失敗を呼び出し元へ返す。"""
        try:
            completed = subprocess.run(
                ["tmux", *arguments], input=stdin, capture_output=True,
                timeout=COMMAND_TIMEOUT_SECONDS, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(
                f"tmux制御を実行できません: session={self._session_name}, operation={arguments[0]}"
            ) from error
        if completed.returncode != 0:
            raise RuntimeError(
                f"tmux制御に失敗しました: session={self._session_name}, operation={arguments[0]}, "
                f"exit_code={completed.returncode}"
            )
