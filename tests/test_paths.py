from pathlib import Path

import pytest

from dotlingo import paths


def test_source_ui_file_stays_next_to_the_package() -> None:
    index = paths.app_resource("web/index.html")
    assert index.is_file()
    assert index.parent.name == "web"


def test_frozen_ui_file_uses_the_packaged_package_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = tmp_path / "dotlingo" / "web" / "index.html"
    index.parent.mkdir(parents=True)
    index.write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert paths.app_resource("web/index.html") == index
    assert paths.app_resource("assets/app_icon.ico") == (
        tmp_path / "dotlingo" / "assets" / "app_icon.ico"
    )
