"""ダッシュボードの動作状態遷移を一元管理する。

ARGOSの動作状態(録音中・考え中・読み上げ中・ロック中など)は、PTTコールバック、
録音ワーカー、TTSワーカー、認証警告ループ、各種監視スレッドなど複数の並行スレッドから
更新される。単純な「最後に書いた人勝ち」だと、進行中の対話の状態を背景スレッドや
前ターンの後片付けが上書きしてしまう競合が起きる。

StatusController は状態遷移を1つのロック下に集約し、次の規律を課すことでこれを防ぐ:

- 前景の対話には世代トークンを割り当て、現行世代の更新だけを反映する(stale な後片付けを無視)。
- 対話が状態を持っている間(``_active``)、背景スレッドは休止状態を書き込めない。
- 休止状態(待機/ロック/警戒)は認証状態と警戒フラグから一意に決まる。
"""

from __future__ import annotations

import threading
from typing import Callable

from argos.services.dashboard.state import DashboardState


READY = ("ready", "待機中")
LOCKED = ("locked", "ロック中")
ALERT = ("alert", "警戒中")


class StatusController:
    """動作状態の遷移を一元管理し、並行スレッド間の競合を防ぐ。"""

    def __init__(self, state: DashboardState, is_authenticated: Callable[[], bool]) -> None:
        """状態ストアと認証状態の問い合わせ関数を保持する。"""
        self._state = state
        self._is_authenticated = is_authenticated
        self._lock = threading.RLock()
        self._generation = 0
        self._active = False
        self._alert = False

    def invalidate(self) -> int:
        """進行中の前景対話を無効化し、新しい世代トークンを返す。

        音声キャンセルと同期して呼び出す。以降、古い世代からの状態更新は反映されない。
        """
        with self._lock:
            self._generation += 1
            return self._generation

    def current_generation(self) -> int:
        """現在の世代トークンを返す。"""
        with self._lock:
            return self._generation

    def set(self, token: int, code: str, label: str) -> None:
        """指定世代が現行なら前景状態を反映する。stale な世代は無視する。"""
        with self._lock:
            if token != self._generation:
                return
            self._active = True
            self._write_locked(code, label)

    def finish(self, token: int) -> None:
        """前景対話の完了。まだ現行世代なら休止状態へ戻す。stale なら何もしない。"""
        with self._lock:
            if token != self._generation:
                return
            self._active = False
            self._apply_resting_locked()

    def force_resting(self) -> None:
        """世代に依存せず休止状態へ戻す(キャンセル、ミュート、起動完了など)。"""
        with self._lock:
            self._active = False
            self._apply_resting_locked()

    def note_idle_waiting(self) -> None:
        """背景スレッド用。前景対話が無く未認証のときだけ休止状態を反映する。

        認証済み、または対話が状態を持っている間は何もしないため、警告ループや
        認証監視が録音中・本人確認中などの前景状態を上書きしない。
        """
        with self._lock:
            if self._active or self._is_authenticated():
                return
            self._apply_resting_locked()

    def set_alert_mode(self, alert: bool) -> None:
        """警戒モードフラグを更新する(表示は休止状態遷移時に反映される)。"""
        with self._lock:
            self._alert = alert

    def mark_alert(self) -> None:
        """警戒モードに入り、前景対話が無ければ即時に警戒表示へ切り替える。"""
        with self._lock:
            self._alert = True
            if not self._active:
                self._write_locked(*ALERT)

    def set_display(self, code: str, label: str) -> None:
        """世代に依存しない一過性の状態表示(起動中、処理エラーなど)。"""
        with self._lock:
            self._write_locked(code, label)

    def _apply_resting_locked(self) -> None:
        """認証状態と警戒フラグから休止状態を決めて反映する。"""
        if self._is_authenticated():
            self._alert = False
            self._write_locked(*READY)
        elif self._alert:
            self._write_locked(*ALERT)
        else:
            self._write_locked(*LOCKED)

    def _write_locked(self, code: str, label: str) -> None:
        """状態コードが変化する場合だけ状態ストアへ反映する。

        キャッシュではなく実状態を参照するため、外部イベントAPIやテストが直接
        set_status した場合とも整合し、不要な再描画とSSE通知を避ける。
        """
        if code == self._state.status_code():
            return
        self._state.set_status(code, label)
