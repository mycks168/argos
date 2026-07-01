from argos.hardware.button import ButtonPtt, PttState


class FakeTimer:
    """テスト内で任意のタイミングで発火できるタイマー。"""

    timers = []

    def __init__(self, _interval, callback):
        """コールバックを保存し、明示的な発火まで実行しない。"""
        self.callback = callback
        self.cancelled = False
        FakeTimer.timers.append(self)

    def start(self):
        """実タイマーのstart互換メソッド。"""
        return None

    def cancel(self):
        """タイマーをキャンセル済みにする。"""
        self.cancelled = True

    def fire(self):
        """キャンセルされていなければ保存したコールバックを実行する。"""
        if not self.cancelled:
            self.callback()


def test_long_press_calls_release(monkeypatch):
    events = []
    times = iter([10.0, 11.0])
    monkeypatch.setattr("argos.hardware.button.time.monotonic", lambda: next(times))
    button = ButtonPtt(
        on_press=lambda: events.append("press"),
        on_release=lambda: events.append("release"),
        on_double_click=lambda: events.append("double"),
        on_cancel=lambda: events.append("cancel"),
    )

    button.handle_press()
    button.handle_release()

    assert events == ["press", "release"]
    assert button.state == PttState.BUSY


def test_double_click_switches_without_recording(monkeypatch):
    events = []
    times = iter([1.00, 1.10, 1.30, 1.40])
    monkeypatch.setattr("argos.hardware.button.time.monotonic", lambda: next(times))
    button = ButtonPtt(
        on_press=lambda: events.append("press"),
        on_release=lambda: events.append("release"),
        on_double_click=lambda: events.append("double"),
        on_cancel=lambda: events.append("cancel"),
    )

    button.handle_press()
    button.handle_release()
    button.handle_press()
    button.handle_release()

    assert events == ["press", "cancel", "press", "cancel", "double"]
    assert button.state == PttState.IDLE


def test_short_press_cancels_recording(monkeypatch):
    events = []
    times = iter([1.00, 1.10])
    monkeypatch.setattr("argos.hardware.button.time.monotonic", lambda: next(times))
    button = ButtonPtt(
        on_press=lambda: events.append("press"),
        on_release=lambda: events.append("release"),
        on_double_click=lambda: events.append("double"),
        on_cancel=lambda: events.append("cancel"),
    )

    button.handle_press()
    button.handle_release()

    assert events == ["press", "cancel"]
    assert button.state == PttState.IDLE


def test_short_press_can_be_recorded_when_requested(monkeypatch):
    """本人確認中などは短い押下でも録音として処理できる。"""
    events = []
    times = iter([1.00, 1.10])
    monkeypatch.setattr("argos.hardware.button.time.monotonic", lambda: next(times))
    FakeTimer.timers = []
    monkeypatch.setattr("argos.hardware.button.Timer", FakeTimer)
    button = ButtonPtt(
        on_press=lambda: events.append("press"),
        on_release=lambda: events.append("release"),
        on_double_click=lambda: events.append("double"),
        on_cancel=lambda: events.append("cancel"),
        should_record_short_press=lambda: True,
    )

    button.handle_press()
    button.handle_release()

    assert events == ["press"]
    assert button.state == PttState.IDLE
    FakeTimer.timers[-1].fire()

    assert events == ["press", "release"]
    assert button.state == PttState.BUSY


def test_recorded_short_press_double_click_switches_before_auth_release(monkeypatch):
    """本人確認用の短押し中でも2回押しなら録音を破棄してスロット切替する。"""
    events = []
    times = iter([1.00, 1.10, 1.30, 1.40])
    monkeypatch.setattr("argos.hardware.button.time.monotonic", lambda: next(times))
    FakeTimer.timers = []
    monkeypatch.setattr("argos.hardware.button.Timer", FakeTimer)
    button = ButtonPtt(
        on_press=lambda: events.append("press"),
        on_release=lambda: events.append("release"),
        on_double_click=lambda: events.append("double"),
        on_cancel=lambda: events.append("cancel"),
        should_record_short_press=lambda: True,
    )

    button.handle_press()
    button.handle_release()
    button.handle_press()
    button.handle_release()

    assert FakeTimer.timers[-1].cancelled is True
    assert events == ["press", "cancel", "double"]
    assert button.state == PttState.IDLE


def test_busy_press_starts_listening():
    events = []
    button = ButtonPtt(
        on_press=lambda: events.append("press"),
        on_release=lambda: events.append("release"),
        on_double_click=lambda: events.append("double"),
        on_cancel=lambda: events.append("cancel"),
    )

    button.mark_busy()
    button.handle_press()

    assert events == ["press"]
    assert button.state == PttState.LISTENING


def test_busy_press_can_continue_to_recording_release(monkeypatch):
    events = []
    times = iter([20.0, 21.0])
    monkeypatch.setattr("argos.hardware.button.time.monotonic", lambda: next(times))
    button = ButtonPtt(
        on_press=lambda: events.append("press"),
        on_release=lambda: events.append("release"),
        on_double_click=lambda: events.append("double"),
        on_cancel=lambda: events.append("cancel"),
    )

    button.mark_busy()
    button.handle_press()
    button.handle_release()

    assert events == ["press", "release"]
    assert button.state == PttState.BUSY


def test_previous_processing_finish_does_not_clear_new_recording(monkeypatch):
    events = []
    times = iter([40.0, 42.0])
    monkeypatch.setattr("argos.hardware.button.time.monotonic", lambda: next(times))
    button = ButtonPtt(
        on_press=lambda: events.append("press"),
        on_release=lambda: events.append("release"),
        on_double_click=lambda: events.append("double"),
        on_cancel=lambda: events.append("cancel"),
    )

    button.mark_busy()
    button.handle_press()
    button.mark_idle()
    button.handle_release()

    assert events == ["press", "release"]
    assert button.state == PttState.BUSY


def test_busy_short_press_cancels_recording(monkeypatch):
    events = []
    times = iter([30.0, 30.1])
    monkeypatch.setattr("argos.hardware.button.time.monotonic", lambda: next(times))
    button = ButtonPtt(
        on_press=lambda: events.append("press"),
        on_release=lambda: events.append("release"),
        on_double_click=lambda: events.append("double"),
        on_cancel=lambda: events.append("cancel"),
    )

    button.mark_busy()
    button.handle_press()
    button.handle_release()

    assert events == ["press", "cancel"]
    assert button.state == PttState.IDLE
