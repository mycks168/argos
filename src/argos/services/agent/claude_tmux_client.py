"""既存の会話経路へ常駐エージェントを接続する試験クライアント。"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shlex
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from argos.config import AgentSlot, Settings, resolve_agent_slot_command, resolve_agent_slot_model
from argos.services.agent.claude_tmux_session import (
    ClaudeTmuxSession, ClaudeTmuxSpec, ClaudeTmuxState, write_private_json,
)
from argos.services.agent.claude_tmux_events import read_events
from argos.services.agent.tmux_input import encode_terminal_text
from argos.services.agent.tmux_commands import interactive_command


POLL_INTERVAL_SECONDS = 0.2
STARTUP_TIMEOUT_SECONDS = 60.0
PASTE_SETTLE_SECONDS = 0.3


def claude_command(settings: Settings, slot: AgentSlot) -> tuple[str, ...]:
    """フックを無効化する起動方法を試験スロットでは拒否する。"""
    arguments = (
        *shlex.split(resolve_agent_slot_command(settings, slot)),
        "--permission-mode", "bypassPermissions", *slot.extra_args,
    )
    reserved = {
        "-p", "--print", "--bare", "--safe-mode", "--settings", "--session-id", "--resume",
        "-r", "--continue", "-c", "--fork-session", "--bg", "--background",
        "--input-format", "--output-format", "--include-partial-messages",
    }
    if any(argument.split("=", 1)[0] in reserved for argument in arguments):
        raise ValueError("Claude tmux試験ではprint・resume・フック設定を上書きする引数は使えません")
    model = resolve_agent_slot_model(settings, slot)
    return (*arguments, "--model", model) if model else arguments


class ClaudeTmuxClient:
    """常駐ターンの制御を共有する。公開済みのクラス名は互換性のため維持する。"""

    def __init__(self, settings: Settings, slot: AgentSlot) -> None:
        """スロットの起動仕様と所有ファイルの場所を決定する。"""
        self._slot = slot
        self._console_ready_session_id = ""
        self.current_model = resolve_agent_slot_model(settings, slot)
        key = hashlib.sha256(json.dumps([slot.name, slot.provider, slot.cwd]).encode()).hexdigest()
        directory = Path(settings.agent_state_path).expanduser().parent / "tmux-slots" / key
        provider = slot.provider.strip().lower()
        command = claude_command(settings, slot) if provider in {"claude", "claudecode"} else interactive_command(settings, slot)
        native_provider = "claude" if provider == "claudecode" else provider
        environment = (("CODEX_HOME", str(Path(settings.codex_home).expanduser())),) if provider == "codex" and settings.codex_home else ()
        self._session = ClaudeTmuxSession(directory, ClaudeTmuxSpec(slot.name, slot.cwd, command, native_provider, environment))

    @property
    def terminal_session_name(self) -> str:
        """ローカル手動試験でattachするための所有セッション名を返す。"""
        state = self._session.saved_state()
        return state.session_name if state is not None else ""

    @property
    def current_name(self) -> str:
        """既存のスロット選択へ表示する名前を返す。"""
        return self._slot.name

    @property
    def current_provider(self) -> str:
        """既存の会話経路で使用するproviderを返す。"""
        return self._slot.provider

    def next_slot(self) -> str:
        """単一スロットなので選択は変更しない。"""
        return self.current_name

    def select_slot(self, name: str, provider: str) -> str:
        """別スロットへの誤入力を拒否する。"""
        if (name, provider) != (self.current_name, self.current_provider):
            raise ValueError("Claude tmux試験の対象スロットが一致しません")
        return self.current_name

    def ask(self, prompt: str) -> str:
        """会話API互換の最終回答を返す。"""
        return "".join(self.ask_stream(prompt))

    def ask_slot_stream(self, name: str, provider: str, prompt: str) -> Iterable[str]:
        """明示されたスロットへ入力する。"""
        self.select_slot(name, provider)
        return self.ask_stream(prompt)

    def reset_current(self) -> None:
        """明示的なリセットで所有セッションだけを終了する。"""
        with self._exclusive_turn():
            self._session.reset()

    def terminal_target(self) -> ClaudeTmuxState:
        """表示の接続では会話のロックを奪わず、同じClaudeへattachする。"""
        state, _is_created = self._session.open()
        return state

    def console_input(self, data: bytes, write: Callable[[], None]) -> None:
        """音声ターンへの割込みと、端末の入力途中への音声の混入を防ぐ。"""
        if re.fullmatch(rb"\x1b\[[?>0-9;]*[cnRt]|\x1b\](10|11);rgb:[0-9a-fA-F/]+\x1b\\", data):
            write()
            return
        with self._exclusive_turn():
            state = self.terminal_target()
            if self._console_ready_session_id != state.session_id:
                self._wait_ready(state)
                self._console_ready_session_id = state.session_id
            self._finish_manual_input(state)
            path = self._session.directory / "manual.json"
            pending = json.loads(path.read_text()) if path.exists() else {
                "offset": self._session.journal_path.stat().st_size if self._session.journal_path.exists() else 0,
                "is_submitted": False,
            }
            if pending["is_submitted"]:
                raise RuntimeError("端末から送った応答を待っています。入力は再送しません")
            pending["is_submitted"] = data.endswith(b"\r")
            write_private_json(path, json.dumps(pending))
            if data == b"\r":
                # CLIごとの拡張キー交渉に左右されず、表示済み本文を確実に送信する。
                time.sleep(PASTE_SETTLE_SECONDS)
                self._session.transport(state).submit()
            else:
                write()
            if data == b"\x15":
                path.unlink()

    def _finish_manual_input(self, state: ClaudeTmuxState) -> None:
        """端末から送った応答を、次の音声ターンの回答と取り違えない。"""
        path = self._session.directory / "manual.json"
        if not path.exists():
            return
        offset = json.loads(path.read_text())["offset"]
        events, _next_offset = read_events(self._session.journal_path, offset)
        if any(event.session_id == state.session_id and event.event in {"Stop", "StopFailure"} for event in events):
            path.unlink()

    def ask_stream(self, prompt: str) -> Iterable[str]:
        """別Runnerプロセスからの同時入力もファイルロックで拒否する。"""
        encode_terminal_text(prompt)
        with self._exclusive_turn():
            yield from self._ask_locked(prompt)

    def observe_stream(self) -> Iterable[str]:
        """端末で既に送信された発言の回答だけを受け取り、再送しない。"""
        with self._exclusive_turn():
            state = self.terminal_target()
            path = self._session.directory / "manual.json"
            if not path.exists():
                raise RuntimeError("端末からの送信が見つかりません")
            pending = json.loads(path.read_text())
            if not pending["is_submitted"]:
                raise RuntimeError("端末の入力はまだ送信されていません")
            answer = self._wait_answer(state, pending["offset"])
            path.unlink()
            yield answer

    @contextmanager
    def _exclusive_turn(self) -> Iterator[None]:
        """会話とリセットで同じロックを使い、実行中の破棄を防ぐ。"""
        descriptor = os.open(self._session.directory / "turn.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("このtmuxスロットは応答処理中です") from error
            yield

    def _ask_locked(self, prompt: str) -> Iterable[str]:
        """中断した入力を再送せず、人の確認が必要な状態として残す。"""
        pending_path = self._session.directory / "pending.json"
        if pending_path.exists():
            raise RuntimeError("前のtmuxターンが未確定です。二重送信せずターミナルを確認してください")
        state, _is_created = self._session.open()
        self._finish_manual_input(state)
        if (self._session.directory / "manual.json").exists():
            raise RuntimeError("端末で入力中または応答待ちです。端末の会話を終えてから音声入力してください")
        self._wait_ready(state)
        journal = self._session.journal_path
        offset = journal.stat().st_size
        write_private_json(pending_path, json.dumps({"offset": offset, "session_id": state.session_id}))
        transport = self._session.transport(state)
        transport.send_text(prompt)
        time.sleep(PASTE_SETTLE_SECONDS)
        transport.submit()
        answer = self._wait_answer(state, offset)
        pending_path.unlink()
        yield answer

    def _wait_ready(self, state: ClaudeTmuxState) -> None:
        """信頼確認などで起動できない場合は、入力を押し込まず停止する。"""
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        offset = 0
        while time.monotonic() < deadline:
            events, offset = read_events(self._session.journal_path, offset)
            startup = [event for event in events if event.session_id == state.session_id and event.event in {"SessionStart", "StopFailure"}]
            if startup:
                if startup[0].event == "StopFailure":
                    raise RuntimeError("エージェントの接続確認が失敗しました。ターミナルを確認してください")
                time.sleep(PASTE_SETTLE_SECONDS)
                return
            self._session.verify(state)
            time.sleep(POLL_INTERVAL_SECONDS)
        raise RuntimeError(f"エージェントの起動を確認できません。信頼確認やログインを端末で確認してください: {state.session_name}")

    def _wait_answer(self, state: ClaudeTmuxState, offset: int) -> str:
        """自分のセッションの新しいStopだけを確定回答として取得する。"""
        timeout_seconds = self._slot.tmux_timeout_seconds
        started = time.monotonic()
        while timeout_seconds == 0 or time.monotonic() - started < timeout_seconds:
            events, offset = read_events(self._session.journal_path, offset)
            for event in events:
                if event.session_id != state.session_id:
                    continue
                if event.event == "StopFailure" or (event.event == "Stop" and not event.text.strip()):
                    raise RuntimeError("エージェントが回答を確定できませんでした。ターミナルを確認してください")
                if event.event == "Stop":
                    return event.text
            self._session.verify(state)
            time.sleep(POLL_INTERVAL_SECONDS)
        raise RuntimeError("エージェントの応答待ちが設定時間を超えました。処理は再送していません")
