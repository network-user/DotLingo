from dotlingo.app import _ask_window_to_close, _console_should_stop


def test_console_stop_covers_ctrl_c_and_console_close() -> None:
    assert _console_should_stop(0)  # Ctrl+C
    assert _console_should_stop(1)  # Ctrl+Break
    assert _console_should_stop(2)  # закрытие окна консоли
    assert not _console_should_stop(3)


def test_console_close_uses_the_ui_thread_when_the_form_exists() -> None:
    posted: list[object] = []

    class Native:
        def BeginInvoke(self, action) -> None:
            posted.append(action)

        def Close(self) -> None:
            posted.append("close")

    window = type("Window", (), {"native": Native(), "destroy": lambda self: None})()
    _ask_window_to_close(window)
    assert posted
    posted[0]()
    assert posted[-1] == "close"


def test_console_close_destroys_the_window_before_the_form_exists() -> None:
    calls: list[str] = []

    class Window:
        native = None

        def destroy(self) -> None:
            calls.append("destroy")

    _ask_window_to_close(Window())
    assert calls == ["destroy"]
