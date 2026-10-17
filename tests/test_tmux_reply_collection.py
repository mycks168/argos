"""通常回答との排他、再起動、ジョブ保存の再試行を合成通知で確認する。"""

import fcntl
import json
import threading
import uuid
from dataclasses import asdict, replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from argos.config import AgentSlot, Settings
from argos.services.agent.background_answers import BackgroundAnswer, BackgroundAnswers
from argos.services.agent.claude_tmux_client import ClaudeTmuxClient
from argos.services.agent.claude_tmux_events import read_events, record_event
from argos.services.agent.claude_tmux_session import ClaudeTmuxState
from argos.services.agent.parent_replies import ParentReplies
from argos.services.agent.runner import AgentJobStore, AgentRunner, AgentRunnerServer
from argos.services.agent.tmux_reply_collector import TmuxReplyCollector
from argos.services.agent.tmux_transport import TmuxSlotIdentity
from test_agent_runner import _settings

CollectionSetup = tuple[Settings, ClaudeTmuxClient, ClaudeTmuxState, ParentReplies, AgentJobStore]


@pytest.fixture
def setup_collection(tmp_path: Path) -> CollectionSetup:
    """利用中のCLIを起動しない、正しい所有IDのスロットと保存先を作る。"""
    settings = _settings(tmp_path)
    slot = AgentSlot("作業", "claude", str(tmp_path), execution_mode="tmux")
    settings = replace(settings, agent_slots=(slot,))
    client = ClaudeTmuxClient(settings, slot)
    generation = str(uuid.uuid4())
    state = ClaudeTmuxState(generation, TmuxSlotIdentity(slot.name, slot.provider, slot.cwd, generation).session_name, "%27", client._session._spec.fingerprint)
    directory = client._session.directory
    (directory / "session.json").write_text(json.dumps(asdict(state)))
    source = ParentReplies(directory, generation)
    source.initialize(0)
    store = AgentJobStore(tmp_path / "runner")
    return settings, client, state, source, store


def append_stop(client: ClaudeTmuxClient, generation: str, text: str) -> int:
    """一つの完了行の保存直後位置を返す。"""
    record_event(client._session.journal_path, json.dumps({"session_id":generation, "hook_event_name":"Stop", "last_assistant_message":text}))
    return client._session.journal_path.stat().st_size


def test_later_parent_is_a_single_durable_job(setup_collection: CollectionSetup) -> None:
    """最初の通常回答は除外し、後の親回答だけ再起動後も一つのジョブにする。"""
    settings, client, state, source, store = setup_collection
    first = append_stop(client, state.session_id, "通常の回答")
    source.taken(first)
    append_stop(client, state.session_id, "後で届いた回答")
    collector = TmuxReplyCollector(settings, store)
    collector.poll()
    jobs = store.list_undelivered()
    assert len(jobs) == 1 and jobs[0].session_id == state.session_id
    job = jobs[0]
    assert Path(job.result_path).read_text() == "後続の回答です。\n後で届いた回答"
    assert Path(job.result_path).stat().st_mode & 0o777 == 0o600
    assert jobs[0].response_target == "background"
    assert client.background_ids() == (job.job_id,)
    TmuxReplyCollector(settings, AgentJobStore(Path(settings.agent_runner_state_dir))).poll()
    assert [current.job_id for current in store.list_undelivered()] == [job.job_id]
    store.mark_delivered(job.job_id)
    store.prune_delivered(0, now=10**12)
    collector.poll()
    assert store.list_undelivered() == []
    with client.background_answer(job.job_id) as text:
        assert text.startswith("後続の回答です。")


def test_normal_pending_and_turn_lock_are_not_stolen(setup_collection: CollectionSetup) -> None:
    """実行中・未回収の通常回答を後続回答として二重配送しない。"""
    settings, client, state, source, store = setup_collection
    offset = append_stop(client, state.session_id, "通常")
    pending = client._session.directory / "pending.json"
    pending.write_text(json.dumps({"session_id":state.session_id, "offset":0}))
    collector = TmuxReplyCollector(settings, store)
    collector.poll()
    assert store.list_undelivered() == []
    source.taken(offset)
    pending.unlink()
    with (client._session.directory / "turn.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        append_stop(client, state.session_id, "遅着")
        collector.poll()
        assert store.list_undelivered() == []
    collector.poll()
    assert len(store.list_undelivered()) == 1


def test_sink_failure_can_retry_without_loss(setup_collection: CollectionSetup) -> None:
    """ジョブ保存後・既読前の障害でも同じIDで回収し直す。"""
    settings, client, state, _source, store = setup_collection
    append_stop(client, state.session_id, "再試行対象")
    sink = Mock()
    def persist_then_fail(slot: AgentSlot, answer: BackgroundAnswer) -> None:
        """耐久的に保存した直後の停止を模擬する。"""
        store.accept_reply(slot, answer)
        raise OSError("模擬停止")
    sink.accept_reply.side_effect = persist_then_fail
    TmuxReplyCollector(settings, sink).poll()
    original = store.list_undelivered()[0].job_id
    TmuxReplyCollector(settings, store).poll()
    assert [job.job_id for job in store.list_undelivered()] == [original]


def test_local_origin_does_not_leave_duplicate_browser_audio(setup_collection: CollectionSetup) -> None:
    """本体PTT起点の後続回答は本体側の既存回収経路へ渡す。"""
    settings, client, state, _source, store = setup_collection
    client._remember_response_target(state, "local")
    append_stop(client, state.session_id, "本体向け")
    TmuxReplyCollector(settings, store).poll()
    assert store.list_undelivered()[0].response_target == "background_local"
    assert client.background_ids() == ()


def test_initial_baseline_and_generation(setup_collection: CollectionSetup) -> None:
    """導入前の既存履歴・別世代のStopを無条件に読み上げ直さない。"""
    settings, client, state, source, store = setup_collection
    source.cursor_path.unlink()
    append_stop(client, state.session_id, "導入前")
    collector = TmuxReplyCollector(settings, store)
    collector.poll()
    assert store.list_undelivered() == []
    append_stop(client, "other", "別世代")
    collector.poll()
    assert store.list_undelivered() == []
    assert read_events(client._session.journal_path, 0)[0][-1].end_offset > 0


def test_collector_start_and_stop(setup_collection: CollectionSetup) -> None:
    """定期監視を終了イベントで止め、通常CLI・リモートスロットを操作しない。"""
    settings, client, state, _source, store = setup_collection
    append_stop(client, state.session_id, "自動回収")
    stop = threading.Event()
    collector = TmuxReplyCollector(settings, store)
    thread = collector.start(stop)
    stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    collector.settings = replace(settings, agent_slots=(replace(settings.agent_slots[0], execution_mode="cli"),))
    collector.poll()


def test_native_readiness_and_missing_session(setup_collection: CollectionSetup) -> None:
    """起動確認Stopや、既にリセットされた所有状態を配送しない。"""
    settings, client, state, _source, store = setup_collection
    directory = client._session.directory
    assert not TmuxReplyCollector._is_ready(directory, state.session_id, "codex")
    (directory / "native.json").write_text(json.dumps({"generation":state.session_id, "is_ready":False}))
    assert not TmuxReplyCollector._is_ready(directory, state.session_id, "codex")
    (directory / "native.json").write_text(json.dumps({"generation":state.session_id, "is_ready":True}))
    assert TmuxReplyCollector._is_ready(directory, state.session_id, "codex")
    (directory / "session.json").unlink()
    TmuxReplyCollector(settings, store).poll()
    assert not store.list_undelivered()


def test_browser_consumed_reply_is_not_imported(setup_collection: CollectionSetup, monkeypatch: pytest.MonkeyPatch) -> None:
    """一覧取得後にブラウザが取得した回答を再配送せず、他の回収を続ける。"""
    settings, client, state, _source, store = setup_collection
    answers = BackgroundAnswers(client._session.directory)
    answers.record(state.session_id, "child", "worker", "先にブラウザへ")
    event_id = answers.pending(state.session_id)[0]
    answers.acknowledge(event_id)
    monkeypatch.setattr(ClaudeTmuxClient, "background_ids", lambda _self: (event_id,))
    TmuxReplyCollector(settings, store).poll()
    assert not store.list_undelivered()


def test_other_generation_reservation_does_not_block(setup_collection: CollectionSetup) -> None:
    """旧世代の未入力記録を、現在世代の回答の予約として扱わない。"""
    settings, client, state, _source, store = setup_collection
    (client._session.directory / "manual.json").write_text(json.dumps({"session_id":"old", "offset":0}))
    append_stop(client, state.session_id, "現在の回答")
    TmuxReplyCollector(settings, store).poll()
    assert len(store.list_undelivered()) == 1


def test_imported_reply_uses_existing_authenticated_api(setup_collection: CollectionSetup) -> None:
    """認証付きの既存claim・結果取得・配信済み通知で後続回答を回収する。"""
    settings, client, state, _source, store = setup_collection
    append_stop(client, state.session_id, "既存APIへ")
    TmuxReplyCollector(settings, store).poll()
    api = AgentRunnerServer(AgentRunner(settings, store), token="test-token")
    server = ThreadingHTTPServer(("127.0.0.1", 0), api._build_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    headers = {"Authorization":"Bearer test-token", "X-Argos-Delivery-Owner":"test-owner"}
    try:
        assert requests.get(base_url + "/api/jobs", timeout=2).status_code == 401
        claimed = requests.post(base_url + "/api/jobs/claim", headers=headers, timeout=2)
        assert claimed.status_code == 200
        job = claimed.json()["jobs"][0]
        assert job["session_id"] == state.session_id
        assert job["response_target"] == "background"
        result = requests.get(base_url + "/api/jobs/" + job["job_id"], headers=headers, timeout=2)
        assert result.json()["result"] == "後続の回答です。\n既存APIへ"
        delivered = requests.post(base_url + "/api/jobs/" + job["job_id"] + "/deliver", headers=headers, timeout=2)
        assert delivered.status_code == 200
        assert not store.list_undelivered()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
