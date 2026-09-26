from __future__ import annotations

import errno
from pathlib import Path

from dotlingo.preferences import load_preferences, save_preferences


def test_save_preferences_flushes_and_atomically_round_trips(tmp_path: Path) -> None:
    expected = {"setup_seen": True, "reduce_motion": False, "last_project": "project-1"}

    save_preferences(expected, tmp_path)

    assert load_preferences(tmp_path) == expected
    assert (tmp_path / "preferences.json").is_file()
    assert list(tmp_path.glob("preferences.json.*.tmp")) == []


def test_save_preferences_survives_windows_bad_descriptor_fsync(
    tmp_path: Path, monkeypatch
) -> None:
    expected = {"setup_seen": True, "reduce_motion": False, "last_project": "project-ebadf"}

    def unsupported_fsync(_descriptor: int) -> None:
        raise OSError(errno.EBADF, "Bad file descriptor")

    monkeypatch.setattr("dotlingo.preferences.os.fsync", unsupported_fsync)

    save_preferences(expected, tmp_path)

    assert load_preferences(tmp_path) == expected
