from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotlingo.paths import user_data_root

DEFAULTS: dict[str, Any] = {
    "setup_seen": False,
    "reduce_motion": True,
    "last_project": "",
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
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps({**DEFAULTS, **values}, ensure_ascii=False, indent=2), encoding="utf-8")
    with temp_path.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temp_path, path)
