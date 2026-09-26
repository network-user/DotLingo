from __future__ import annotations

import threading

from dotlingo.app import DotLingoApp
from dotlingo.model_download import DownloadCancellation


class FakeRoot:
    def __init__(self) -> None:
        self.callback = None
        self.destroyed = False

    def after(self, _delay: int, callback):
        self.callback = callback
        return "scheduled-close-check"

    def after_cancel(self, _job: str) -> None:
        self.callback = None

    def destroy(self) -> None:
        self.destroyed = True


class FakeStatus:
    def __init__(self) -> None:
        self.text = ""

    def configure(self, *, text: str) -> None:
        self.text = text


def test_close_waits_for_model_download_worker_before_finalizing() -> None:
    release_worker = threading.Event()
    worker = threading.Thread(target=release_worker.wait, daemon=True)
    worker.start()
    root = FakeRoot()
    status = FakeStatus()
    cancel = DownloadCancellation()
    app = DotLingoApp.__new__(DotLingoApp)
    app.root = root
    app.page = "projects"
    app._closing = False
    app._pending_close = False
    app._close_wait_job = None
    app._close_cancel_accepted = None
    app._download_worker = worker
    app._download_cancel = cancel
    app._download_window = None
    app._overlay = None
    app.sidebar_status = status
    app.project_queues = {}
    app._confirm_review_change = lambda: True
    app._confirm_settings_change = lambda: True
    app._stop_page_animation = lambda: None
    finalized: list[bool] = []
    app._finalize_close = lambda: finalized.append(True)

    app.close()

    assert app._pending_close
    assert cancel.is_set()
    assert not finalized
    assert "Отмена загрузки запрошена" in status.text

    release_worker.set()
    worker.join(timeout=1)
    assert root.callback is not None
    root.callback()

    assert not app._pending_close
    assert finalized == [True]
