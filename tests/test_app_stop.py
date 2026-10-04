from dotlingo.app import (
    _ask_window_to_close,
    _closing_result,
    _console_should_stop,
    _install_webview_browser_arguments,
    _release_webview_without_waiting,
)


def test_webview_cache_release_does_not_wait_for_the_browser() -> None:
    from webview import _state
    from webview.platforms import edgechromium

    class WebView:
        def __init__(self) -> None:
            self.disposed = False

        def Dispose(self) -> None:
            self.disposed = True

    previous = _state["private_mode"]
    _state["private_mode"] = True
    try:
        _install_webview_browser_arguments()
        view = WebView()
        _release_webview_without_waiting(type("Browser", (), {"webview": view})())
    finally:
        _state["private_mode"] = previous
    assert edgechromium.EdgeChrome.clear_user_data is _release_webview_without_waiting
    assert view.disposed is False


def test_closing_keeps_the_window_until_the_book_shuts() -> None:
    assert _closing_result("animate", True) is False
    assert _closing_result("wait", False) is False
    assert _closing_result("allow", True) is None
    assert _closing_result("animate", False) is None


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


def test_finish_close_permits_the_window_to_go(tmp_path) -> None:
    from dotlingo.api import Api

    api = Api(tmp_path)
    calls: list[str] = []
    api.set_window_closer(lambda: calls.append("close"))
    assert api.note_close_attempt() == "animate"
    assert api.note_close_attempt() == "wait"
    assert api.finishClose()["ok"] is True
    assert api.close_permitted is True
    assert calls == ["close"]
    assert api.note_close_attempt() == "allow"


def test_console_close_destroys_the_window_before_the_form_exists() -> None:
    calls: list[str] = []

    class Window:
        native = None

        def destroy(self) -> None:
            calls.append("destroy")

    _ask_window_to_close(Window())
    assert calls == ["destroy"]
