from __future__ import annotations

import tkinter as tk

import pytest

from dotlingo.app import DotLingoApp
from dotlingo.preferences import save_preferences


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


def test_first_run_wizard_is_an_in_application_overlay(tmp_path, monkeypatch) -> None:
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tcl/Tk GUI runtime is unavailable: {exc}")
    save_preferences({"setup_seen": True}, tmp_path)
    monkeypatch.setattr("dotlingo.app.detect", lambda _models_dir: None)
    app = DotLingoApp(root, tmp_path)
    root.update_idletasks()

    app.open_setup_wizard()

    assert app._overlay is not None
    assert app._overlay.winfo_toplevel() is root
    assert app._overlay.winfo_class() == "Frame"
    assert root.grab_current() == app._overlay
    app.close()
