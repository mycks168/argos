"""agyの生成結果と子Stopの両方が一致した場合だけ回答を回収する。"""

import json
from pathlib import Path

import pytest

from argos.services.agent.agy_subagents import (
    record_agy_child_event,
    refresh_agy_children,
)
from argos.services.agent.background_answers import BackgroundAnswers

CHILD_ID = "12345678-1234-1234-1234-123456789abc"


def setup_parent(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    """本物の会話とは独立した親世代と生成通知を作る。"""
    directory = tmp_path / "slot"
    directory.mkdir()
    (directory / "session.json").write_text('{"session_id":"generation"}')
    (directory / "native.json").write_text('{"native_id":"parent"}')
    transcript = tmp_path / "brain/parent/.system_generated/logs/transcript_full.jsonl"
    transcript.parent.mkdir(parents=True)
    payload = {
        "conversationId": "parent",
        "toolCall": {"name": "invoke_subagent"},
        "transcriptPath": str(transcript.with_name("transcript.jsonl")),
        "stepIdx": 2,
    }
    return directory, transcript, payload


def write_link(transcript: Path, child_id: str = CHILD_ID, uri: str = "") -> Path:
    """生成ツールが返す子IDとログURIを模した行を作る。"""
    child_log = (
        transcript.parents[3] / child_id / ".system_generated/logs/transcript.jsonl"
    )
    content = "Created the following subagents:\n" + json.dumps(
        {"conversationId": child_id, "logAbsoluteUri": uri or child_log.as_uri()}
    )
    transcript.write_text(
        json.dumps({"step_index": 2, "type": "GENERIC", "content": content}) + "\n"
    )
    return child_log


def test_delayed_link_and_child_stop(tmp_path: Path) -> None:
    """生成ログが遅れても、子のStopを親の完了と混同せず回収する。"""
    directory, transcript, payload = setup_parent(tmp_path)
    assert record_agy_child_event(directory, "generation", "SubagentLink", payload)
    child_log = write_link(transcript)
    child_log.parent.mkdir(parents=True)
    stop = {
        "conversationId": CHILD_ID,
        "transcriptPath": str(child_log.with_name("transcript_full.jsonl")),
        "fullyIdle": True,
    }
    assert record_agy_child_event(directory, "generation", "Stop", stop)
    refresh_agy_children(directory, "generation")
    assert BackgroundAnswers(directory).pending("generation") == ()
    child_log.with_name("transcript_full.jsonl").write_text(
        json.dumps(
            {
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "step_index": 1,
                "content": "子の回答",
            }
        )
        + "\n"
    )
    refresh_agy_children(directory, "generation")
    store = BackgroundAnswers(directory)
    (event_id,) = store.pending("generation")
    with store.consume(event_id, "generation") as answer:
        assert answer.text == "子の回答"
    refresh_agy_children(directory, "generation")
    assert store.pending("generation") == ()
    refresh_agy_children(directory, "other")


def test_agy_event_boundaries(tmp_path: Path) -> None:
    """親の通常通知と古い世代を区別し、未生成の子を公開しない。"""
    directory, _transcript, payload = setup_parent(tmp_path)
    with pytest.raises(ValueError, match="JSONオブジェクト"):
        record_agy_child_event(directory, "generation", "Stop", [])
    assert record_agy_child_event(directory, "other", "Stop", payload)
    assert not record_agy_child_event(directory, "generation", "Stop", payload)
    assert record_agy_child_event(
        directory, "generation", "SubagentLink", {**payload, "conversationId": "other"}
    )
    assert record_agy_child_event(
        directory, "generation", "SessionStart", {"conversationId": CHILD_ID}
    )
    assert record_agy_child_event(
        directory,
        "generation",
        "Stop",
        {"conversationId": CHILD_ID, "fullyIdle": False},
    )
    assert record_agy_child_event(
        directory, "generation", "Stop", {"conversationId": 1, "fullyIdle": True}
    )
    (directory / "native.json").unlink()
    assert not record_agy_child_event(directory, "generation", "Stop", payload)


@pytest.mark.parametrize(
    "change",
    [{"toolCall": {}}, {"stepIdx": "bad"}, {"transcriptPath": "/wrong/log.jsonl"}],
)
def test_invalid_link_notification(tmp_path: Path, change: dict[str, object]) -> None:
    """別ツールは無視し、不正な親ログやstepでファイルを読まない。"""
    directory, _transcript, payload = setup_parent(tmp_path)
    if "toolCall" in change:
        record_agy_child_event(
            directory, "generation", "SubagentLink", {**payload, **change}
        )
    else:
        with pytest.raises(ValueError):
            record_agy_child_event(
                directory, "generation", "SubagentLink", {**payload, **change}
            )


@pytest.mark.parametrize(
    "child_id,uri", [("bad", ""), (CHILD_ID, "file:///etc/passwd")]
)
def test_child_path_must_match(tmp_path: Path, child_id: str, uri: str) -> None:
    """親の生成結果から別のファイルを読み出せない。"""
    directory, transcript, payload = setup_parent(tmp_path)
    write_link(transcript, child_id, uri)
    with pytest.raises(ValueError):
        record_agy_child_event(directory, "generation", "SubagentLink", payload)


def test_agy_storage_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """通知を黙って捨てず、保存上限を明示して失敗する。"""
    directory, transcript, payload = setup_parent(tmp_path)
    child_log = write_link(transcript)
    monkeypatch.setattr("argos.services.agent.agy_subagents.MAX_AGY_CHILDREN", 0)
    with pytest.raises(RuntimeError, match="子生成通知"):
        record_agy_child_event(directory, "generation", "SubagentLink", payload)
    with pytest.raises(RuntimeError, match="子完了通知"):
        record_agy_child_event(
            directory,
            "generation",
            "Stop",
            {
                "conversationId": CHILD_ID,
                "transcriptPath": str(child_log),
                "fullyIdle": True,
            },
        )
    monkeypatch.setattr("argos.services.agent.agy_subagents.MAX_AGY_CHILDREN", 1)
    links = directory / "agy-child-links"
    links.mkdir()
    (links / "other.json").write_text("{}")
    with pytest.raises(RuntimeError, match="子対応"):
        record_agy_child_event(directory, "generation", "SubagentLink", payload)


def test_agy_log_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """不完全な行と別の結果を読み飛ばし、巨大ログは拒否する。"""
    directory, transcript, payload = setup_parent(tmp_path)
    transcript.write_text('{"step_index":2,"type":"GENERIC","content":"no child"}\n{')
    record_agy_child_event(directory, "generation", "SubagentLink", payload)
    monkeypatch.setattr("argos.services.agent.agy_subagents.MAX_EVENT_BYTES", 1)
    with pytest.raises(ValueError, match="ログ行"):
        record_agy_child_event(directory, "generation", "SubagentLink", payload)
    monkeypatch.setattr("argos.services.agent.agy_subagents.MAX_TRANSCRIPT_BYTES", 1)
    with pytest.raises(ValueError, match="ログが"):
        record_agy_child_event(directory, "generation", "SubagentLink", payload)


def test_agy_incomplete_binding(tmp_path: Path) -> None:
    """子のStopがない場合や、別世代・別パスの場合は公開しない。"""
    directory, transcript, payload = setup_parent(tmp_path)
    child_log = write_link(transcript)
    record_agy_child_event(directory, "generation", "SubagentLink", payload)
    refresh_agy_children(directory, "generation")
    with pytest.raises(ValueError, match="名前"):
        record_agy_child_event(
            directory,
            "generation",
            "Stop",
            {
                "conversationId": CHILD_ID,
                "transcriptPath": str(child_log.with_name("wrong")),
                "fullyIdle": True,
            },
        )
    record_agy_child_event(
        directory,
        "generation",
        "Stop",
        {
            "conversationId": CHILD_ID,
            "transcriptPath": str(child_log),
            "fullyIdle": True,
        },
    )
    refresh_agy_children(directory, "other")
    assert BackgroundAnswers(directory).pending("generation") == ()
