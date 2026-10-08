from __future__ import annotations

import errno
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from dotlingo.paths import user_data_root

DEFAULTS: dict[str, Any] = {
    "setup_seen": False,
    "reduce_motion": False,
    "theme": "dark",
    "last_project": "",
    "sidebar_collapsed": False,
    "sidebar_width": 252,
    "chat_list_width": 240,
    "chat_list_height": 200,
    "chat_list_hidden": False,
    "ui_scale": 100,
    "text_scale": 100,
    "translate_source": "auto",
    "translate_target": "ru",
    "translate_suffix": "",
    "translate_model": "",
    "translate_context": "",
    "translate_glossary": True,
    "translate_surface": "file",
    "update_check_enabled": True,
    "update_auto_prompt": True,
}


def _settings_path(root: Path | None = None) -> Path:
    base = Path(root) if root is not None else user_data_root()
    return base / "preferences.json"


def load_preferences(root: Path | None = None) -> dict[str, Any]:
    try:
        values = json.loads(_settings_path(root).read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            return dict(DEFAULTS)
        return {**DEFAULTS, **values}
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULTS)


def save_preferences(values: dict[str, Any], root: Path | None = None) -> None:
    path = _settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the temporary file beside the destination so os.replace stays atomic on Windows.
    # fsync needs a writable descriptor on Windows, so flush and sync the open writer before
    # closing it and atomically activating the new settings.
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f"{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        json.dump({**DEFAULTS, **values}, handle, ensure_ascii=False, indent=2)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError as exc:
            # Some Windows file handles/filesystems reject _commit with EBADF even though
            # flush succeeded. Preferences remain safe to replace atomically in that case.
            if exc.errno != errno.EBADF:
                raise
    os.replace(temp_path, path)
