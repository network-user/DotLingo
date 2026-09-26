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
    """Resolve a packaged resource in source and PyInstaller layouts."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative
