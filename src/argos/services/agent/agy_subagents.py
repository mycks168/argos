"""agyの生成ツール結果を根拠に、子のStopを所有スロットへ結び付ける。"""

from __future__ import annotations

import json
import re
import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from argos.services.agent.background_answers import BackgroundAnswers
from argos.services.agent.claude_tmux_events import MAX_EVENT_BYTES
from argos.services.agent.claude_tmux_session import write_private_json
from argos.services.agent.tmux_native_events import (
    MAX_TRANSCRIPT_BYTES,
    _read_agy_answer,
)

MAX_AGY_CHILDREN = 64


def record_agy_child_event(
    directory: Path, generation: str, event: str, payload: object
) -> bool:
    """親Stopは既存処理へ戻し、子の通知は専用記録だけに保存する。"""
    with _child_records_lock(directory):
        return _record_child_event(directory, generation, event, payload)


@contextmanager
def _child_records_lock(directory: Path) -> Iterator[None]:
    """親・子のフックと端末readによる同じ対応表の並行更新を防ぐ。"""
    descriptor = os.open(directory / "agy-children.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def _record_child_event(
    directory: Path, generation: str, event: str, payload: object
) -> bool:
    """保存された親ID以外の通知は通常ターンへ渡さない。"""
    if not isinstance(payload, dict):
        raise ValueError("agyの子通知はJSONオブジェクトにしてください")
    state = json.loads((directory / "session.json").read_text())
    native_path = directory / "native.json"
    is_stale = str(state["session_id"]) != generation
    if is_stale or not native_path.exists():
        return is_stale
    parent_id = json.loads(native_path.read_text())["native_id"]
    if event == "SubagentLink":
        if payload.get("conversationId") == parent_id:
            _record_links(directory, generation, payload)
        return True
    if payload.get("conversationId") == parent_id:
        return False
    if (
        event == "Stop"
        and payload.get("fullyIdle") is True
        and not payload.get("error")
    ):
        child_id = payload.get("conversationId")
        path = payload.get("transcriptPath")
        if (
            isinstance(child_id, str)
            and isinstance(path, str)
            and re.fullmatch(r"[a-f0-9-]{36}", child_id)
        ):
            stops = directory / "agy-child-stops"
            stops.mkdir(mode=0o700, exist_ok=True)
            if (
                not (stops / f"{child_id}.json").exists()
                and len(list(stops.glob("*.json"))) >= MAX_AGY_CHILDREN
            ):
                raise RuntimeError("agyの子完了通知が保存上限です")
            transcript = Path(path)
            if transcript.name not in {"transcript.jsonl", "transcript_full.jsonl"}:
                raise ValueError("agyの子完了ログの名前が不正です")
            write_private_json(
                stops / f"{child_id}.json",
                json.dumps(
                    {
                        "generation": generation,
                        "path": str(transcript.with_name("transcript.jsonl")),
                    }
                ),
            )
    return True


def _record_links(directory: Path, generation: str, payload: dict[str, object]) -> None:
    """同じ親のinvoke_subagentツール結果の行以外をリンク根拠にしない。"""
    call = payload.get("toolCall")
    if not isinstance(call, dict) or call.get("name") != "invoke_subagent":
        return
    transcript, step = payload.get("transcriptPath"), payload.get("stepIdx")
    if not isinstance(transcript, str) or type(step) is not int:
        raise ValueError("agy生成ツールの通知が不正です")
    path = Path(transcript).with_name("transcript_full.jsonl")
    if len(path.parents) < 4 or path.parents[2].name != payload.get("conversationId"):
        raise ValueError("agy生成ツールと親ログの会話が一致しません")
    _remember_request(directory, generation, step, payload)
    _scan_links(directory, generation, step, path)


def _remember_request(
    directory: Path, generation: str, step: int, payload: dict[str, object]
) -> None:
    """フックよりログの書込みが遅れても、後のreadで照合を再開する。"""
    requests = directory / "agy-link-requests"
    requests.mkdir(mode=0o700, exist_ok=True)
    request_path = requests / f"{generation}-{step}.json"
    if not request_path.exists():
        if len(list(requests.glob("*.json"))) >= MAX_AGY_CHILDREN:
            raise RuntimeError("agyの子生成通知が保存上限です")
        write_private_json(
            request_path, json.dumps({"generation": generation, "payload": payload})
        )


def _scan_links(directory: Path, generation: str, step: int, path: Path) -> None:
    """親ログの指定ツール結果だけを読み、途中書込みは次回へ回す。"""
    if not path.exists():
        return
    if path.stat().st_size > MAX_TRANSCRIPT_BYTES:
        raise ValueError("agy生成ツールのログが読込上限です")
    with path.open("rb") as source:
        while line := source.readline(MAX_EVENT_BYTES + 1):
            if len(line) > MAX_EVENT_BYTES:
                raise ValueError("agy生成ツールのログ行が読込上限です")
            if not line.endswith(b"\n"):
                continue
            item = json.loads(line)
            if item.get("step_index") == step and item.get("type") == "GENERIC":
                _save_links(directory, generation, str(item.get("content", "")), path)


def _save_links(
    directory: Path, generation: str, content: str, parent_transcript: Path
) -> None:
    """ログ内の生成結果JSONと、同じbrain配下の子ログだけを許可する。"""
    prefix = "Created the following subagents:"
    if prefix not in content:
        return
    remainder = content.split(prefix, 1)[1]
    decoder = json.JSONDecoder()
    links = directory / "agy-child-links"
    links.mkdir(mode=0o700, exist_ok=True)
    for _attempt in range(MAX_AGY_CHILDREN):
        start = remainder.find("{")
        if start < 0:
            break
        child, end = decoder.raw_decode(remainder[start:])
        remainder = remainder[start + end :]
        child_id, uri = child.get("conversationId", ""), child.get("logAbsoluteUri", "")
        if (
            not isinstance(child_id, str)
            or re.fullmatch(r"[a-f0-9-]{36}", child_id) is None
        ):
            raise ValueError("agyの子識別子が不正です")
        expected = (
            parent_transcript.parents[3]
            / child_id
            / ".system_generated/logs/transcript.jsonl"
        )
        parsed = urlparse(uri)
        if parsed.scheme != "file" or parsed.netloc or Path(parsed.path) != expected:
            raise ValueError("agyの子ログが生成結果の場所と一致しません")
        if (
            not (links / f"{child_id}.json").exists()
            and len(list(links.glob("*.json"))) >= MAX_AGY_CHILDREN
        ):
            raise RuntimeError("agyの子対応が保存上限です")
        write_private_json(
            links / f"{child_id}.json",
            json.dumps({"generation": generation, "path": str(expected)}),
        )


def refresh_agy_children(directory: Path, generation: str) -> None:
    """生成結果と子Stopの両方がそろった回答だけを公開する。"""
    with _child_records_lock(directory):
        _refresh_children_locked(directory, generation)


def _refresh_children_locked(directory: Path, generation: str) -> None:
    """遅延した生成ログと完了通知を同一のスナップショットで照合する。"""
    for request_path in (directory / "agy-link-requests").glob("*.json"):
        request = json.loads(request_path.read_text())
        if request["generation"] == generation:
            _record_links(directory, generation, request["payload"])
    for path in (directory / "agy-child-links").glob("*.json"):
        stop_path = directory / "agy-child-stops" / path.name
        if not stop_path.exists():
            continue
        link, stop = json.loads(path.read_text()), json.loads(stop_path.read_text())
        if (
            link["generation"] != generation
            or stop["generation"] != generation
            or link["path"] != stop["path"]
        ):
            continue
        transcript = Path(link["path"]).with_name("transcript_full.jsonl")
        progress = _read_agy_answer(transcript, -1)
        if progress.text:
            BackgroundAnswers(directory).record(
                generation, path.stem, "antigravity", progress.text
            )
