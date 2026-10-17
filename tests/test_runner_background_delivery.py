"""Runner経由とブラウザ経由の後続回答を同じ履歴へ反映する。"""

from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from argos.core.app import ArgosApp, _TerminalGateway
from test_app import _patch_app, _settings


def test_background_history_matches_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """先にRunnerが回収してもブラウザ取得で同じ回答を重複表示しない。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    save = Mock()
    monkeypatch.setattr(app._conversation_store, "save_histories", save)
    event_id = "a" * 64
    text = "後続の回答です。\n合成した回答"
    app._deliver_runner_result(event_id, "作業", "codex", text, "background")
    gateway = _TerminalGateway(app)
    gateway._console = Mock()
    gateway._console.background.return_value = nullcontext(text)
    list(gateway.background_turn(event_id, False, "作業", "codex"))
    app._deliver_runner_result(event_id, "作業", "codex", text, "background")
    messages = app._dashboard_state.slot_messages("作業", "codex")
    assert len(messages) == 1
    assert messages[0]["id"] == "subagent-" + event_id
    assert messages[0]["text"] == text
    assert not app._pending_slot_speech
    assert save.call_count >= 2


def test_local_replies_queue_without_overwriting(monkeypatch: pytest.MonkeyPatch) -> None:
    """複数の完了が続いても本体音声待ちを上書きせず、即時再生もしない。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    monkeypatch.setattr(app._conversation_store, "save_histories", Mock())
    start = Mock()
    monkeypatch.setattr(app, "_start_pending_slot_response", start)
    app._deliver_runner_result("a" * 64, "作業", "codex", "最初", "background_local")
    app._deliver_runner_result("b" * 64, "作業", "codex", "次", "background_local")
    app._deliver_runner_result("a" * 64, "作業", "codex", "最初", "background_local")
    assert list(app._pending_slot_speech.values()) == ["最初\n次"]
    assert len(app._dashboard_state.slot_messages("作業", "codex")) == 2
    start.assert_not_called()


def test_background_history_save_failure_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    """メモリへ反映済みでも履歴の再保存を省略しない。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    save = Mock(side_effect=[OSError("模擬保存失敗"), None])
    monkeypatch.setattr(app._conversation_store, "save_histories", save)
    with pytest.raises(OSError, match="模擬保存失敗"):
        app._deliver_runner_result("a" * 64, "作業", "codex", "保存対象", "background")
    app._deliver_runner_result("a" * 64, "作業", "codex", "保存対象", "background")
    assert save.call_count == 2
    assert len(app._dashboard_state.slot_messages("作業", "codex")) == 1


@pytest.mark.parametrize(("is_stale", "has_save_failure"), [(False, True), (True, False), (False, False)])
def test_monitor_acknowledges_only_persisted_current_reply(monkeypatch: pytest.MonkeyPatch, is_stale: bool, has_save_failure: bool) -> None:
    """保存失敗は未配信のままにし、リセット前の回答は新しい履歴へ混ぜない。"""
    _patch_app(monkeypatch)
    app = ArgosApp(_settings())
    agent = Mock()
    agent.list_undelivered.return_value = [{
        "job_id":"a" * 64, "slot_name":"作業", "provider":"codex", "status":"completed",
        "result":"後続の回答", "response_target":"background", "session_id":"test-generation",
    }]
    app._agent = agent
    monkeypatch.setattr(app, "_matches_reply_generation", lambda *_arguments: not is_stale)
    monkeypatch.setattr(app._conversation_store, "save_histories", Mock(side_effect=OSError("模擬障害") if has_save_failure else None))
    monkeypatch.setattr(app, "_is_speech_busy", lambda: True)
    start = Mock()
    monkeypatch.setattr(app, "_start_pending_slot_response", start)
    monkeypatch.setattr(app._shutdown, "wait", lambda _timeout: app._shutdown.set())
    app._run_agent_delivery_monitor()
    if is_stale:
        agent.mark_delivered.assert_called_once_with("a" * 64)
        assert not app._dashboard_state.slot_messages("作業", "codex")
    elif has_save_failure:
        agent.mark_delivered.assert_not_called()
        assert len(app._dashboard_state.slot_messages("作業", "codex")) == 1
    else:
        agent.mark_delivered.assert_called_once_with("a" * 64)
        assert len(app._dashboard_state.slot_messages("作業", "codex")) == 1
    start.assert_not_called()
