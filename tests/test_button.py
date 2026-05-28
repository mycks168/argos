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

    assert events == ["press", "press", "double"]
    assert button.state == PttState.IDLE


def test_busy_press_cancels():
    events = []
    button = ButtonPtt(
        on_press=lambda: events.append("press"),
        on_release=lambda: events.append("release"),
        on_double_click=lambda: events.append("double"),
        on_cancel=lambda: events.append("cancel"),
    )

    button.mark_busy()
    button.handle_press()

    assert events == ["cancel"]
    assert button.state == PttState.IDLE

