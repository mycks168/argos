"""PeriodicMonitor の起動・繰り返し・停止のテスト。"""

from __future__ import annotations

import threading
import time

from argos.core.periodic_monitor import PeriodicMonitor


def test_periodic_monitor_runs_immediately_then_stops():
    """起動直後に一度実行し、shutdownで停止する。"""
    shutdown = threading.Event()
    calls: list[float] = []
    monitor = PeriodicMonitor("test", 0.01, lambda: calls.append(time.monotonic()), shutdown)

    monitor.start()
    time.sleep(0.05)
    shutdown.set()
    time.sleep(0.02)

    # 起動直後に最低1回、間隔0.01秒で複数回実行される
    assert len(calls) >= 2


def test_periodic_monitor_does_not_run_after_shutdown():
    """shutdown済みなら一度も実行しない。"""
    shutdown = threading.Event()
    shutdown.set()
    calls: list[int] = []
    monitor = PeriodicMonitor("test", 0.01, lambda: calls.append(1), shutdown)

    monitor.start()
    time.sleep(0.03)

    assert calls == []


def test_periodic_monitor_stops_promptly_on_shutdown():
    """長い間隔でも shutdown で待機が解除されて停止する。"""
    shutdown = threading.Event()
    calls: list[int] = []
    monitor = PeriodicMonitor("test", 60.0, lambda: calls.append(1), shutdown)

    monitor.start()
    time.sleep(0.02)
    shutdown.set()
    monitor._thread.join(timeout=1)

    # 起動直後の1回だけ実行され、長い間隔の待機は shutdown で解除される
    assert calls == [1]
    assert not monitor._thread.is_alive()
