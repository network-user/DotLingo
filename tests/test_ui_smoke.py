from __future__ import annotations

import tkinter as tk

import pytest

from dotlingo.app import DotLingoApp


def test_main_window_and_navigation_smoke(tmp_path) -> None:
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tcl/Tk GUI runtime is unavailable: {exc}")
    app = DotLingoApp(root, tmp_path, smoke=True)
    assert set(app.nav_buttons) == {"projects", "documents", "queue", "review", "models", "glossary", "settings"}
    for page, _ in (
        ("projects", "Projects"), ("documents", "Documents"), ("queue", "Queue"),
        ("models", "Models"), ("glossary", "Glossary"), ("settings", "Settings"),
    ):
        app.show_page(page)
        root.update_idletasks()
    app.close()
