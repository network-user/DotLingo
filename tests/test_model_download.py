from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any

import pytest

import dotlingo.model_download as downloader
from dotlingo.model_download import (
    DownloadCancellation,
    DownloadCancelled,
    ModelDownloadError,
    download_model,
)
from dotlingo.models import catalog, installed, verify_model


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        status: int,
        headers: dict[str, str],
        cancel_after_read: DownloadCancellation | None = None,
        cancel_after_eof: DownloadCancellation | None = None,
    ) -> None:
        self.body = body
        self.status = status
        self.headers = headers
        self.position = 0
        self.cancel_after_read = cancel_after_read
        self.cancel_after_eof = cancel_after_eof

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def geturl(self) -> str:
        return "https://huggingface.co/org/repo/resolve/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/file.gguf"

    def getcode(self) -> int:
        return self.status

    def read(self, count: int) -> bytes:
        value = self.body[self.position : self.position + count]
        self.position += len(value)
        if value and self.cancel_after_read:
            self.cancel_after_read.set()
            self.cancel_after_read = None
        elif not value and self.cancel_after_eof:
            self.cancel_after_eof.set()
            self.cancel_after_eof = None
        return value


def _model(payload: bytes) -> dict[str, Any]:
    return {
        "id": "tiny-test-model",
        "status": "available",
        "revision": "a" * 40,
        "filename": "tiny.gguf",
        "repo": "org/repo",
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def test_catalog_only_allows_download_for_pinned_gguf_artifacts() -> None:
    records = catalog()
    available = [item for item in records if item["status"] == "available"]
    assert {item["id"] for item in available} == {
        "hy-mt2-1.8b-q4km",
        "hy-mt2-1.8b-q8",
        "hy-mt2-7b-q4km",
    }
    for model in available:
        assert len(model["revision"]) == 40
        assert len(model["sha256"]) == 64
        assert model["size_bytes"] > 0
        assert model["format"] == "GGUF"
        assert model["prompt_style"] == "hy-mt2"
        assert model["sampling"]["temperature"] == 0.7
        assert model["append_no_think"] is False
    codes = [tuple(model["ui_language_codes"]) for model in available]
    assert codes[0] == codes[1] == codes[2]
    assert "ru" in codes[0] and "en" in codes[0] and "zh-Hant" in codes[0]
    assert len(codes[0]) == 38


def test_download_resumes_partial_and_activates_only_after_integrity_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"GGUF-small-fixture"
    model = _model(payload)
    partial = payload[:7]
    cancel = DownloadCancellation()
    requests: list[Any] = []

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        requests.append(request)
        if len(requests) == 1:
            return FakeResponse(partial, 200, {"Content-Length": str(len(payload))}, cancel)
        assert request.get_header("Range") == f"bytes={len(partial)}-"
        return FakeResponse(
            payload[len(partial) :],
            206,
            {
                "Content-Length": str(len(payload) - len(partial)),
                "Content-Range": f"bytes {len(partial)}-{len(payload) - 1}/{len(payload)}",
            },
        )

    monkeypatch.setattr(downloader.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(DownloadCancelled):
        download_model(model, tmp_path, cancel=cancel)
    staged = tmp_path / model["id"] / "tiny.gguf.part"
    assert staged.read_bytes() == partial
    assert not (tmp_path / model["id"] / "tiny.gguf").exists()

    cancel = DownloadCancellation()
    destination = download_model(model, tmp_path, cancel=cancel)
    assert destination.read_bytes() == payload
    verify_model(destination, model)
    assert installed(model, tmp_path)
    assert not staged.exists()


def test_bad_download_hash_never_activates_weight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"GGUF-expected"
    model = _model(payload)
    model["sha256"] = "0" * 64
    monkeypatch.setattr(
        downloader.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(b"GGUF-expecteX", 200, {"Content-Length": "13"}),
    )
    with pytest.raises(ValueError, match="SHA-256"):
        download_model(model, tmp_path, cancel=DownloadCancellation())
    assert not (tmp_path / model["id"] / model["filename"]).exists()


def test_download_stops_before_network_when_disk_space_is_insufficient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"GGUF-small-fixture"
    model = _model(payload)
    monkeypatch.setattr(downloader.shutil, "disk_usage", lambda _: type("Usage", (), {"free": 0})())

    def unexpected_network(*_: Any, **__: Any) -> None:
        raise AssertionError("network should not be opened when disk space is insufficient")

    monkeypatch.setattr(downloader.urllib.request, "urlopen", unexpected_network)
    with pytest.raises(ModelDownloadError, match="свободного места"):
        download_model(model, tmp_path, cancel=DownloadCancellation())


def test_late_cancel_after_eof_prevents_model_activation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"GGUF-small-fixture"
    model = _model(payload)
    cancel = DownloadCancellation()
    monkeypatch.setattr(
        downloader.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(
            payload,
            200,
            {"Content-Length": str(len(payload))},
            cancel_after_eof=cancel,
        ),
    )

    with pytest.raises(DownloadCancelled, match="отменена"):
        download_model(model, tmp_path, cancel=cancel)

    assert (tmp_path / model["id"] / "tiny.gguf.part").read_bytes() == payload
    assert not (tmp_path / model["id"] / "tiny.gguf").exists()


def test_cancel_token_rejects_cancellation_after_activation_begins() -> None:
    cancel = DownloadCancellation()

    assert cancel.begin_activation()
    assert not cancel.set()
    assert not cancel.is_set()


def test_existing_verified_model_repairs_installed_marker(tmp_path: Path) -> None:
    payload = b"GGUF-small-fixture"
    model = _model(payload)
    target = tmp_path / model["id"] / model["filename"]
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)

    result = download_model(model, tmp_path, cancel=DownloadCancellation())

    assert result == target
    assert installed(model, tmp_path)


def test_cancel_during_existing_model_verification_does_not_report_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"GGUF-small-fixture"
    model = _model(payload)
    target = tmp_path / model["id"] / model["filename"]
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)
    cancel = DownloadCancellation()

    def verify_then_cancel(_path: Path, _model: dict[str, Any]) -> None:
        cancel.set()

    monkeypatch.setattr(downloader, "verify_model", verify_then_cancel)
    with pytest.raises(DownloadCancelled):
        download_model(model, tmp_path, cancel=cancel)

    assert not (target.parent / "installed.json").exists()
    assert target.read_bytes() == payload


def test_download_requires_atomic_cancellation_token(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="atomic activation"):
        download_model(_model(b"GGUF-small-fixture"), tmp_path, cancel=threading.Event())
