from argos.hardware.button import ButtonPtt, PttState


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
