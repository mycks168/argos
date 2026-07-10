"""StatusController の状態遷移と競合防止のテスト。"""

from __future__ import annotations

from argos.core.status_controller import StatusController
from argos.services.dashboard.state import DashboardState


class _Auth:
    """認証状態を差し替え可能にする簡易スタブ。"""

    def __init__(self, authenticated: bool = False) -> None:
        self.authenticated = authenticated

    def __call__(self) -> bool:
        return self.authenticated


def _code(state: DashboardState) -> str:
    return state.snapshot()["status"]["code"]


def test_set_reflects_current_generation():
    """現行世代の前景状態は反映される。"""
    state = DashboardState()
    controller = StatusController(state, _Auth(authenticated=True))
    token = controller.current_generation()

    controller.set(token, "listening", "録音中")

    assert _code(state) == "listening"


def test_stale_generation_set_is_ignored():
    """新しい対話が始まった後の古い世代からの更新は無視される。"""
    state = DashboardState()
    controller = StatusController(state, _Auth(authenticated=True))
    old_token = controller.current_generation()
    controller.set(old_token, "thinking", "考え中")

    new_token = controller.invalidate()
    controller.set(new_token, "listening", "録音中")

    # 古い世代からの読み上げ中更新は現在の録音中を上書きしない
    controller.set(old_token, "speaking", "読み上げ中")

    assert _code(state) == "listening"


def test_finish_reverts_to_ready_when_authenticated():
    """認証済みなら対話完了で待機中へ戻す。"""
    state = DashboardState()
    controller = StatusController(state, _Auth(authenticated=True))
    token = controller.current_generation()
    controller.set(token, "speaking", "読み上げ中")

    controller.finish(token)

    assert _code(state) == "ready"


def test_finish_reverts_to_locked_when_unauthenticated():
    """未認証なら対話完了でロック中へ戻す。"""
    state = DashboardState()
    controller = StatusController(state, _Auth(authenticated=False))
    token = controller.current_generation()
    controller.set(token, "authenticating", "本人確認中")

    controller.finish(token)

    assert _code(state) == "locked"


def test_stale_finish_does_not_clobber_new_interaction():
    """前ターンの後片付けは、新しい対話の録音中表示を上書きしない（症状①）。"""
    state = DashboardState()
    controller = StatusController(state, _Auth(authenticated=True))
    old_token = controller.current_generation()

    # 新しいPTT押下で世代が進み、録音中になる
    new_token = controller.invalidate()
    controller.set(new_token, "listening", "録音中")

    # 前ターンのワーカーが遅れて finish しても待機中へ戻さない
    controller.finish(old_token)

    assert _code(state) == "listening"


def test_note_idle_waiting_skips_during_active_interaction():
    """対話が状態を持つ間、背景スレッドは休止状態を書き込まない（症状③）。"""
    state = DashboardState()
    controller = StatusController(state, _Auth(authenticated=False))
    token = controller.current_generation()
    controller.set(token, "authenticating", "本人確認中")

    controller.note_idle_waiting()

    assert _code(state) == "authenticating"


def test_note_idle_waiting_skips_when_authenticated():
    """認証済みなら警告ループの休止状態更新は反映されない（症状③）。"""
    state = DashboardState()
    auth = _Auth(authenticated=False)
    controller = StatusController(state, auth)
    token = controller.current_generation()

    # 対話中に認証が成立し、待機中へ確定する
    auth.authenticated = True
    controller.set(token, "ready", "待機中")
    controller.finish(token)

    # 認証成立後に警告ループが遅れて locked を書こうとしても無視される
    controller.note_idle_waiting()

    assert _code(state) == "ready"


def test_note_idle_waiting_locks_when_idle_and_unauthenticated():
    """待機中に認証が切れたら背景更新でロック中へ戻す。"""
    state = DashboardState()
    controller = StatusController(state, _Auth(authenticated=False))
    controller.force_resting()  # まず休止状態へ（未認証なので locked）
    # 直接 ready にしておき、監視がロックへ戻すことを確認する
    state.set_status("ready", "待機中")

    controller.note_idle_waiting()

    assert _code(state) == "locked"


def test_alert_mode_shows_alert_while_unauthenticated():
    """警戒モードでは未認証の間、休止状態が警戒中になる。"""
    state = DashboardState()
    auth = _Auth(authenticated=False)
    controller = StatusController(state, auth)

    controller.mark_alert()
    assert _code(state) == "alert"

    # 認証が成立すれば警戒は解除され待機中へ
    auth.authenticated = True
    controller.force_resting()
    assert _code(state) == "ready"


def test_force_resting_ignores_generation():
    """キャンセルやミュートは世代に関係なく休止状態へ戻す。"""
    state = DashboardState()
    controller = StatusController(state, _Auth(authenticated=True))
    token = controller.current_generation()
    controller.set(token, "speaking", "読み上げ中")

    controller.invalidate()  # 世代を進める
    controller.force_resting()

    assert _code(state) == "ready"
