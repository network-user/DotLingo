from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "DotLingo"


def user_data_root() -> Path:
    """Return a durable per-user location, separate from the installed program."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / APP_NAME
    return Path.home() / ".local" / "share" / "dotlingo"


def projects_root() -> Path:
    return user_data_root() / "projects"


def models_root() -> Path:
    return user_data_root() / "models"


def app_resource(relative: str) -> Path:
    """Resolve a packaged resource in a checkout and in a PyInstaller build.

    The spec copies package data to ``_internal/dotlingo``. ``sys._MEIPASS`` is
    the ``_internal`` directory, so the frozen base is one level deeper.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS) / "dotlingo"
    else:
        base = Path(__file__).resolve().parent
    return base / relative
