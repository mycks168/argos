"""Claude試験スロットの所有セッションを永続的に対応付ける。"""

from __future__ import annotations

import hashlib
import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from argos.services.agent.tmux_transport import COMMAND_TIMEOUT_SECONDS, TmuxSlotIdentity, TmuxTransport
from argos.services.agent.tmux_commands import agy_environment, codex_hooks


QUESTION_INSTRUCTION = (
    "ユーザーへの確認や質問は通常の文章で返してください。"
    "AskUserQuestionは使用せず、次のユーザー発言を待ってください。"
)


@dataclass(frozen=True)
class ClaudeTmuxSpec:
    """設定変更を検出するための常駐プロセスの起動仕様。"""

    name: str
    cwd: str
    command: tuple[str, ...]
    provider: str = "claude"
    environment: tuple[tuple[str, str], ...] = ()

    @property
    def fingerprint(self) -> str:
        """引数の境界を保った起動仕様の指紋を返す。"""
        fields = asdict(self)
        if not self.environment:
            fields.pop("environment")
        if self.provider == "claude":
            # 既存Claudeの台帳を新しいセッションと誤認しないため指紋を維持する。
            fields.pop("provider")
        return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class ClaudeTmuxState:
    """Runnerがクライアントを再生成しても維持するセッション情報。"""

    session_id: str
    session_name: str
    pane_id: str
    fingerprint: str


def run_tmux(arguments: list[str]) -> str:
    """制御呼び出しに時間制限を付け、本文をエラーへ露出しない。"""
    try:
        result = subprocess.run(
            ["tmux", *arguments], capture_output=True, text=True,
            timeout=COMMAND_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"tmuxを実行できません: operation={arguments[0]}") from error
    if result.returncode != 0:
        raise RuntimeError(f"tmux操作に失敗しました: operation={arguments[0]}")
    return result.stdout.strip()


def write_private_json(path: Path, value: str) -> None:
    """台帳を中途半端な内容にしないよう同じディレクトリで置換する。"""
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(value)
    temporary.replace(path)


class ClaudeTmuxSession:
    """所有するClaudeだけを起動・確認・再利用する。"""

    def __init__(self, directory: Path, spec: ClaudeTmuxSpec) -> None:
        """既存のユーザーセッションとは別の台帳領域を使う。"""
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory = directory
        self._spec = spec
        self._state_path = directory / "session.json"
        self.journal_path = directory / "events.jsonl"

    def open(self) -> tuple[ClaudeTmuxState, bool]:
        """保存済み対応がある場合、別プロセスを勝手に起動し直さない。"""
        descriptor = os.open(self.directory / "lifecycle.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._open_locked()

    def _open_locked(self) -> tuple[ClaudeTmuxState, bool]:
        """ブラウザとRunnerが同時に最初の接続を開いても二重起動しない。"""
        state = self.saved_state()
        if state is not None:
            if state.fingerprint != self._spec.fingerprint:
                raise RuntimeError("tmuxスロットの設定が変わっています。先に試験スロットをリセットしてください")
            self.verify(state)
            return state, False
        return self._start(), True

    def saved_state(self) -> ClaudeTmuxState | None:
        """状態参照のために新たなCLIを起動しない。"""
        if not self._state_path.exists():
            return None
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not all(isinstance(value, str) for value in payload.values()):
                raise ValueError("台帳の型が不正です")
            state = ClaudeTmuxState(**payload)
            uuid.UUID(state.session_id)
            expected = TmuxSlotIdentity(self._spec.name, self._spec.provider, self._spec.cwd, state.session_id)
            if state.session_name != expected.session_name or re.fullmatch(r"%[0-9]+", state.pane_id) is None:
                raise ValueError("台帳のターゲットが不正です")
        except (ValueError, TypeError, OSError) as error:
            raise RuntimeError(f"Claude tmuxの台帳を読み込めません: {self._state_path}") from error
        return state

    def verify(self, state: ClaudeTmuxState) -> None:
        """paneの番号変更や終了を正常な待機状態と取り違えない。"""
        owner = run_tmux(["display-message", "-p", "-t", state.pane_id, "#{session_name}:#{pane_dead}"])
        if owner != f"{state.session_name}:0":
            raise RuntimeError("所有するClaude tmuxセッションが終了または変更されています")

    def transport(self, state: ClaudeTmuxState) -> TmuxTransport:
        """実際に生成されたpane IDへ入力を固定する。"""
        identity = TmuxSlotIdentity(self._spec.name, self._spec.provider, self._spec.cwd, state.session_id)
        return TmuxTransport(identity, state.pane_id)

    def reset(self) -> None:
        """所有権確認後にセッションを終了し、以前のフック記録を保存する。"""
        state = self.saved_state()
        if state is None:
            return
        self.verify(state)
        run_tmux(["kill-session", "-t", f"={state.session_name}"])
        if self.journal_path.exists():
            archive = self.directory / f"events.{state.session_id}.jsonl"
            self.journal_path.rename(archive)
        self._state_path.unlink()
        (self.directory / "pending.json").unlink(missing_ok=True)
        (self.directory / "manual.json").unlink(missing_ok=True)

    def _start(self) -> ClaudeTmuxState:
        """プロンプト本文をシェルへ渡さず、専用Claudeを対話起動する。"""
        session_id = str(uuid.uuid4())
        identity = TmuxSlotIdentity(self._spec.name, self._spec.provider, self._spec.cwd, session_id)
        command, environment = self._launch_arguments(session_id)
        for name, value in self._spec.environment:
            environment += ("-e", f"{name}={value}")
        pane_id = run_tmux([
            "new-session", "-d", "-P", "-F", "#{pane_id}",
            "-s", identity.session_name, "-c", self._spec.cwd, "-x", "120", "-y", "35",
            *environment, *command,
        ])
        state = ClaudeTmuxState(session_id, identity.session_name, pane_id, self._spec.fingerprint)
        write_private_json(self._state_path, json.dumps(asdict(state)))
        return state

    def _launch_arguments(self, session_id: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """CLI固有のフック設定を、同じ所有セッションの管理から分離する。"""
        bootstrap = "接続確認です。ツールを使わず『準備完了』とだけ回答してください。"
        if self._spec.provider == "codex":
            return (*self._spec.command, *codex_hooks(self.directory, session_id), bootstrap), ()
        if self._spec.provider == "antigravity":
            return (*self._spec.command, "--prompt-interactive", bootstrap), agy_environment(self.directory, session_id, self._spec.cwd)
        hook_command = shlex.join([
            sys.executable, "-m", "argos.tools.claude_tmux_hook", str(self.journal_path),
        ])
        settings_path = self.directory / "hooks.json"
        handler = [{"hooks": [{"type": "command", "command": hook_command}]}]
        write_private_json(settings_path, json.dumps({
            "hooks": {event: handler for event in ("SessionStart", "Stop", "StopFailure")},
        }))
        command = (
            *self._spec.command, "--session-id", session_id,
            "--settings", str(settings_path), "--disallowedTools=AskUserQuestion",
            "--append-system-prompt", QUESTION_INSTRUCTION,
        )
        return command, ()
