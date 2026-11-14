"""tmuxの実端末出力を有限バッファでブラウザへ中継する。"""

from __future__ import annotations

import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import termios
import threading
import time
from dataclasses import dataclass

from argos.services.agent.tmux_transport import TerminalSize


MAX_BUFFER_BYTES = 1024 * 1024
READ_CHUNK_BYTES = 32768
IDLE_TIMEOUT_SECONDS = 90
READ_TIMEOUT_SECONDS = 15
WRITE_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class TerminalChunk:
    """再送時にも同じ位置から取得できる端末出力。"""

    data: bytes
    offset: int
    is_closed: bool


class TerminalPty:
    """ブラウザ接続ごとのPTYとattachクライアントを所有する。"""

    def __init__(self, command: tuple[str, ...], size: TerminalSize) -> None:
        """既存tmuxサーバーではなくattachプロセスだけを起動する。"""
        self._condition = threading.Condition()
        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._buffer = bytearray()
        self._base_offset = 0
        self._is_closed = False
        self._last_seen = time.monotonic()
        self._master, slave = pty.openpty()
        environment = {key: value for key, value in os.environ.items() if key not in {"TMUX", "TMUX_PANE"}}
        try:
            self._set_size(size)
            self._process = subprocess.Popen(
                command, stdin=slave, stdout=slave, stderr=slave,
                start_new_session=True, env={**environment, "TERM": "xterm-256color"},
            )
        except BaseException:
            os.close(self._master)
            raise
        finally:
            os.close(slave)
        os.set_blocking(self._master, False)
        self._thread = threading.Thread(target=self._pump, name="terminal-pty", daemon=True)
        self._thread.start()

    @property
    def is_closed(self) -> bool:
        """期限切れ接続を登録簿から回収できるようにする。"""
        with self._condition:
            return self._is_closed

    def read(self, offset: int) -> TerminalChunk:
        """読込位置を進めず保持し、通信断後の再取得を可能にする。"""
        with self._condition:
            self._last_seen = time.monotonic()
            self._validate_offset(offset)
            self._condition.wait_for(
                lambda: self._is_closed or self._base_offset + len(self._buffer) > offset,
                timeout=READ_TIMEOUT_SECONDS,
            )
            self._validate_offset(offset)
            start = offset - self._base_offset
            data = bytes(self._buffer[start:start + READ_CHUNK_BYTES])
            next_offset = offset + len(data)
            return TerminalChunk(data, next_offset, self._is_closed and next_offset == self._base_offset + len(self._buffer))

    def write(self, data: bytes) -> None:
        """生のキー入力をシェル引数にせずPTYへ書き込む。"""
        deadline = time.monotonic() + WRITE_TIMEOUT_SECONDS
        with self._write_lock:
            remaining = memoryview(data)
            while remaining:
                if self.is_closed or time.monotonic() >= deadline:
                    raise RuntimeError("端末へ入力できません。接続を確認してください")
                _, writable, _ = select.select([], [self._master], [], 0.2)
                if writable:
                    count = os.write(self._master, remaining)
                    remaining = remaining[count:]

    def resize(self, size: TerminalSize) -> None:
        """実際のattachクライアントの寸法を変更する。"""
        if self.is_closed:
            raise RuntimeError("端末接続は終了しています")
        self._set_size(size)
        os.kill(self._process.pid, signal.SIGWINCH)

    def close(self) -> None:
        """attachだけを終了し、tmux内のエージェントは残す。"""
        self._stop.set()
        self._thread.join(timeout=3)

    def _set_size(self, size: TerminalSize) -> None:
        """文字の折り返しに使うPTYの実寸を設定する。"""
        fcntl.ioctl(self._master, termios.TIOCSWINSZ, struct.pack("HHHH", size.rows, size.columns, 0, 0))

    def _validate_offset(self, offset: int) -> None:
        """取りこぼしを黙って飛ばさず、再接続による再描画を要求する。"""
        if not self._base_offset <= offset <= self._base_offset + len(self._buffer):
            raise ValueError("端末出力の読込位置が失効しました。再接続してください")

    def _append(self, data: bytes) -> None:
        """遅いクライアントがメモリを無制限に消費しないようにする。"""
        with self._condition:
            self._buffer.extend(data)
            overflow = max(0, len(self._buffer) - MAX_BUFFER_BYTES)
            del self._buffer[:overflow]
            self._base_offset += overflow
            self._condition.notify_all()

    def _pump(self) -> None:
        """UTF-8の途中で区切られても変換せず、元のバイト列を保持する。"""
        try:
            while not self._stop.is_set() and time.monotonic() - self._last_seen < IDLE_TIMEOUT_SECONDS:
                readable, _, _ = select.select([self._master], [], [], 0.2)
                if not readable:
                    continue
                data = os.read(self._master, READ_CHUNK_BYTES)
                if not data:
                    break
                self._append(data)
        except OSError:
            # PTYは子プロセスの終了をEOFではなくEIOで知らせる場合がある。
            self._stop.set()
        finally:
            os.close(self._master)
            self._terminate_client()
            with self._condition:
                self._is_closed = True
                self._condition.notify_all()

    def _terminate_client(self) -> None:
        """tmuxサーバーや他のセッションへシグナルを送らない。"""
        if self._process.poll() is not None:
            return
        try:
            self._process.terminate()
            self._process.wait(timeout=1)
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=1)
