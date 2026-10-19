"""子の回答が通常入力を塞がず、重複・別世代・切断に耐えることを確認する。"""

import json
import io
import sys
from pathlib import Path

import pytest

from argos.services.agent.background_answers import BackgroundAnswers
from argos.services.agent.subagent_hooks import record_subagent
from argos.tools import claude_tmux_hook, tmux_agent_hook


def test_delivery_and_deduplication(tmp_path: Path) -> None:
    """同じ完了通知は一度だけ配信し、別世代の回答を混ぜない。"""
    store = BackgroundAnswers(tmp_path)
    assert store.pending("generation") == ()
    store.record("generation", "child", "Explore", "回答")
    store.record("generation", "child", "Explore", "回答")
    (event_id,) = store.pending("generation")
    assert store.pending("other") == ()
    with pytest.raises(ValueError, match="別世代"):
        with store.consume(event_id, "other"):
            pytest.fail("別世代を配信してはいけない")
    with store.consume(event_id, "generation") as answer:
        assert answer.text == "回答"
        with pytest.raises(RuntimeError, match="回収中"):
            with store.consume(event_id, "generation"):
                pytest.fail("二重配信してはいけない")
    assert store.pending("generation") == ()
    with pytest.raises(ValueError, match="配信済み"):
        with store.consume(event_id, "generation"):
            pytest.fail("配信済みを再生してはいけない")
    assert (store.directory / f"{event_id}.json").stat().st_mode & 0o777 == 0o600


def test_failed_delivery_remains_pending(tmp_path: Path) -> None:
    """TTSや通信が途中で失敗した場合は回答を消費しない。"""
    store = BackgroundAnswers(tmp_path)
    store.record("generation", "child", "Explore", "回答")
    (event_id,) = store.pending("generation")
    with pytest.raises(RuntimeError):
        with store.consume(event_id, "generation"):
            raise RuntimeError("切断")
    assert store.pending("generation") == (event_id,)
    with pytest.raises(ValueError, match="識別子"):
        with store.consume("../session.json", "generation"):
            pytest.fail("不正なパスを開いてはいけない")


def test_limits_and_corruption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """巨大な回答・件数超過・破損データを正常な回答として扱わない。"""
    store = BackgroundAnswers(tmp_path)
    store.record("generation", "", "", "")
    monkeypatch.setattr(
        "argos.services.agent.background_answers.MAX_BACKGROUND_TEXT_BYTES", 2
    )
    with pytest.raises(ValueError, match="保存上限"):
        store.record("generation", "child", "Explore", "too long")
    monkeypatch.setattr(
        "argos.services.agent.background_answers.MAX_BACKGROUND_RECORDS", 0
    )
    with pytest.raises(RuntimeError, match="保存件数"):
        store.record("generation", "child", "Explore", "ok")
    (store.directory / "bad.json").write_text('{"text":1}')
    with pytest.raises(ValueError, match="保存形式"):
        store.pending("generation")


def test_hook_binding(tmp_path: Path) -> None:
    """親・子・世代の対応が一致した完了だけを記録する。"""
    (tmp_path / "session.json").write_text('{"session_id":"generation"}')
    payload = {
        "session_id": "generation",
        "agent_id": "child",
        "agent_type": "Explore",
        "last_assistant_message": "回答",
    }
    record_subagent(tmp_path, "other", payload)
    record_subagent(tmp_path, "generation", {**payload, "session_id": "wrong"})
    assert BackgroundAnswers(tmp_path).pending("generation") == ()
    with pytest.raises(ValueError, match="JSONオブジェクト"):
        record_subagent(tmp_path, "generation", [])
    with pytest.raises(ValueError, match="文字列"):
        record_subagent(tmp_path, "generation", {**payload, "agent_id": 1})
    record_subagent(tmp_path, "generation", payload)
    assert len(BackgroundAnswers(tmp_path).pending("generation")) == 1
    (tmp_path / "native.json").write_text(json.dumps({"native_id": "native"}))
    record_subagent(
        tmp_path,
        "generation",
        {**payload, "session_id": "native", "agent_id": "codex-child"},
    )
    assert len(BackgroundAnswers(tmp_path).pending("generation")) == 2


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_child_hook_command(
    tmp_path: Path, provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """実際のCLI入口も子回答を親のStop台帳へ混ぜない。"""
    (tmp_path / "session.json").write_text('{"session_id":"generation"}')
    payload = {
        "session_id": "generation",
        "agent_id": "child",
        "agent_type": "worker",
        "last_assistant_message": "子の結果",
        "hook_event_name": "SubagentStop",
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    if provider == "claude":
        monkeypatch.setattr(sys, "argv", ["hook", str(tmp_path / "events.jsonl")])
        claude_tmux_hook.main()
        monkeypatch.setattr(sys, "stdin", io.StringIO("xx"))
        monkeypatch.setattr(claude_tmux_hook, "MAX_EVENT_BYTES", 1)
        with pytest.raises(ValueError, match="1MiB"):
            claude_tmux_hook.main()
    else:
        monkeypatch.setattr(
            sys, "argv", ["hook", str(tmp_path), "generation", "codex", "SubagentStop"]
        )
        tmux_agent_hook.main()
    assert len(BackgroundAnswers(tmp_path).pending("generation")) == 1
    assert not (tmp_path / "events.jsonl").exists()


def test_agy_child_hook_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """子から継承されたSessionStartで親のnative IDを上書きしない。"""
    (tmp_path / "session.json").write_text('{"session_id":"generation"}')
    (tmp_path / "native.json").write_text('{"native_id":"parent"}')
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"conversationId":"child"}'))
    monkeypatch.setattr(
        sys,
        "argv",
        ["hook", str(tmp_path), "generation", "antigravity", "SessionStart"],
    )
    tmux_agent_hook.main()
    assert json.loads((tmp_path / "native.json").read_text())["native_id"] == "parent"
