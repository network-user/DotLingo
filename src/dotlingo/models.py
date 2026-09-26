from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from dotlingo.paths import models_root


class ModelIntegrityError(ValueError):
    pass


def catalog() -> list[dict[str, Any]]:
    raw = files("dotlingo").joinpath("models.json").read_text(encoding="utf-8")
    records = json.loads(raw)
    if not isinstance(records, list):
        raise ValueError("Некорректный реестр моделей.")
    return records


def get_model(model_id: str) -> dict[str, Any]:
    for model in catalog():
        if model.get("id") == model_id:
            return model
    raise KeyError(model_id)


def model_path(model: dict[str, Any], root: Path | None = None) -> Path:
    base = Path(root) if root is not None else models_root()
    return base / model["id"] / model["filename"]


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for part in iter(lambda: source.read(chunk_size), b""):
            digest.update(part)
    return digest.hexdigest()


def verify_model(path: Path, model: dict[str, Any]) -> None:
    path = Path(path)
    try:
        size = path.stat().st_size
        with path.open("rb") as file:
            magic = file.read(4)
    except OSError as exc:
        raise ModelIntegrityError("Файл модели не найден или недоступен.") from exc
    if size != int(model["size_bytes"]):
        raise ModelIntegrityError("Размер модели не совпадает с реестром. Файл не активирован.")
    if magic != b"GGUF":
        raise ModelIntegrityError("Файл не является GGUF. Он не будет загружен в runtime.")
    digest = sha256_file(path)
    if digest.lower() != str(model["sha256"]).lower():
        raise ModelIntegrityError("SHA-256 модели не совпадает с закреплённым значением.")


def installed(model: dict[str, Any], root: Path | None = None) -> bool:
    if not model.get("filename") or model.get("size_bytes") is None or not model.get("sha256"):
        return False
    path = model_path(model, root)
    marker = path.parent / "installed.json"
    try:
        metadata = json.loads(marker.read_text(encoding="utf-8"))
        return (
            metadata.get("id") == model["id"]
            and metadata.get("revision") == model["revision"]
            and metadata.get("sha256") == model["sha256"]
            and path.stat().st_size == int(model["size_bytes"])
        )
    except (OSError, ValueError, TypeError):
        return False
