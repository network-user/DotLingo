from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
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


def get_model(model_id: str, root: Path | None = None) -> dict[str, Any]:
    for model in (*catalog(), *market_catalog(root), *custom_catalog(root)):
        if model.get("id") == model_id:
            return model
    raise KeyError(model_id)


def custom_catalog(root: Path | None = None) -> list[dict[str, Any]]:
    """Read user-imported GGUF entries from the local model registry."""
    base = Path(root) if root is not None else models_root()
    try:
        records = json.loads((base / "custom_models.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(records, list):
        return []
    valid = [
        item
        for item in records
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item["id"].startswith("custom-")
        and len(item["id"]) == 19
        and all(char in "0123456789abcdef" for char in item["id"][7:])
        and item.get("filename") == "model.gguf"
        and isinstance(item.get("size_bytes"), int)
        and item["size_bytes"] >= 1024 * 1024
        and isinstance(item.get("sha256"), str)
        and len(item["sha256"]) == 64
        and all(char in "0123456789abcdefABCDEF" for char in item["sha256"])
        and isinstance(item.get("name"), str)
        and bool(item["name"].strip())
        and isinstance(item.get("default_context"), int)
        and item["default_context"] in {2048, 4096, 8192, 16384, 32768}
    ]
    for item in valid:
        try:
            model_file = base / item["id"] / item["filename"]
            item["status"] = (
                "available"
                if model_file.is_file() and model_file.stat().st_size == item["size_bytes"]
                else "missing"
            )
        except OSError:
            item["status"] = "missing"
    return valid


def market_catalog(root: Path | None = None) -> list[dict[str, Any]]:
    """Read GGUF entries downloaded from the curated model market."""
    base = Path(root) if root is not None else models_root()
    try:
        records = json.loads((base / "market_models.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(records, list):
        return []
    return [item for item in records if _valid_market_record(item)]


def upsert_market_model(record: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    """Store one market model record. The caller must already have checked the file metadata."""
    if not _valid_market_record(record):
        raise ValueError("Запись рынка не проходит проверку.")
    base = Path(root) if root is not None else models_root()
    base.mkdir(parents=True, exist_ok=True)
    kept = [item for item in market_catalog(base) if item["id"] != record["id"]]
    registry = [*kept, record]
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=base, prefix="market_models.", suffix=".tmp", delete=False
    ) as handle:
        temp_registry = Path(handle.name)
        json.dump(registry, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_registry, base / "market_models.json")
    return record


def _valid_market_record(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    model_id = item.get("id")
    filename = item.get("filename")
    repo = item.get("repo")
    revision = item.get("revision")
    digest = item.get("sha256")
    if not isinstance(model_id, str) or not re.fullmatch(r"market-[0-9a-f]{12}", model_id):
        return False
    if not isinstance(filename, str) or not _safe_filename(filename):
        return False
    if not isinstance(repo, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}/[A-Za-z0-9][A-Za-z0-9._-]{0,80}",
        repo,
    ):
        return False
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        return False
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        return False
    if not isinstance(item.get("size_bytes"), int) or item["size_bytes"] < 1024 * 1024:
        return False
    if item.get("status") != "available" or item.get("format") != "GGUF":
        return False
    if not isinstance(item.get("name"), str) or not item["name"].strip():
        return False
    return item.get("default_context") in {2048, 4096, 8192, 16384, 32768}


def _safe_filename(name: str) -> bool:
    return (
        Path(name).name == name
        and "/" not in name
        and "\\" not in name
        and name.lower().endswith(".gguf")
        and len(name) <= 180
    )


def all_models(root: Path | None = None) -> list[dict[str, Any]]:
    return [*catalog(), *market_catalog(root), *custom_catalog(root)]


def import_custom_model(
    source: Path,
    name: str,
    language_codes: list[str],
    *,
    root: Path | None = None,
    context_size: int = 4096,
    license_name: str = "Не указана",
) -> dict[str, Any]:
    """Copy and register a user-selected GGUF without touching the source file."""
    from dotlingo.languages import LANGUAGES

    source = Path(source)
    display_name = name.strip()
    codes = list(dict.fromkeys(code.strip().lower() for code in language_codes if code.strip()))
    if not display_name:
        raise ValueError("Укажите название модели.")
    if len(display_name) > 120:
        raise ValueError("Название модели должно быть короче 120 символов.")
    if any(item["name"].casefold() == display_name.casefold() for item in all_models(root)):
        raise ValueError("Модель с таким названием уже есть в списке.")
    if source.suffix.casefold() != ".gguf":
        raise ValueError("Выберите файл с расширением .gguf.")
    if not codes or any(code not in LANGUAGES for code in codes):
        raise ValueError("Укажите хотя бы один известный языковой код, например en,ru.")
    if context_size not in {2048, 4096, 8192, 16384, 32768}:
        raise ValueError("Контекст должен быть одним из поддерживаемых значений.")
    try:
        size = source.stat().st_size
        with source.open("rb") as model_file:
            if model_file.read(4) != b"GGUF":
                raise ValueError("Файл не начинается с сигнатуры GGUF.")
    except OSError as exc:
        raise ValueError("Не удалось прочитать выбранный файл модели.") from exc
    if size < 1024 * 1024:
        raise ValueError("Файл слишком мал, чтобы быть полноценной GGUF-моделью.")

    base = Path(root) if root is not None else models_root()
    base.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(base).free < size + 64 * 1024**2:
        raise ValueError("Недостаточно свободного места для копии модели.")
    model_id = f"custom-{uuid.uuid4().hex[:12]}"
    model_dir = base / model_id
    model_dir.mkdir()
    destination = model_dir / "model.gguf"
    partial = model_dir / "model.gguf.partial"
    shutil.copyfile(source, partial)
    with partial.open("rb") as copied:
        if copied.read(4) != b"GGUF" or partial.stat().st_size != size:
            raise ValueError("Копия модели не прошла первичную проверку.")
    digest = sha256_file(partial)
    os.replace(partial, destination)
    record: dict[str, Any] = {
        "id": model_id,
        "name": display_name,
        "status": "available",
        "family": "Пользовательская",
        "parameters": "не определено",
        "ui_language_codes": codes,
        "ui_language_note": "Список языков задан пользователем; поддержка и качество не проверялись.",
        "language_pairs": [],
        "base_model": "пользовательский файл",
        "repo": "local",
        "revision": model_id,
        "version": "Локальный файл, импортирован пользователем",
        "filename": "model.gguf",
        "format": "GGUF",
        "quantization": "не определено",
        "size_bytes": size,
        "sha256": digest,
        "license": license_name.strip() or "Не указана",
        "license_url": "",
        "card_url": "",
        "runtime": "llama-cpp-python",
        "runtime_adapter": "gguf-llama-cpp-user-import",
        "runtime_range": "Совместимость пользовательского GGUF не проверена",
        "default_context": context_size,
        "max_output_tokens": 1800,
        "languages": "Список языков задан пользователем",
        "estimated_ram_gb": None,
        "estimated_vram_gb": None,
        "memory_estimate_note": "Потребление памяти не определено; проверяйте выбранный контекст.",
        "tested_on_windows": False,
        "notes": "Локальная модель пользователя. Совместимость chat template и качество перевода не проверены.",
        "custom": True,
    }
    marker = destination.parent / "installed.json"
    marker.write_text(
        json.dumps(
            {"id": model_id, "revision": model_id, "sha256": digest},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    registry = [*custom_catalog(base), record]
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=base, prefix="custom_models.", suffix=".tmp", delete=False
    ) as handle:
        temp_registry = Path(handle.name)
        json.dump(registry, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_registry, base / "custom_models.json")
    return record


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
    """Каждый вызов заново читает файл. Кэш по размеру и mtime можно подменить."""
    path = Path(path)
    try:
        stat = path.stat()
        with path.open("rb") as file:
            magic = file.read(4)
    except OSError as exc:
        raise ModelIntegrityError("Файл модели не найден или недоступен.") from exc
    expected = str(model["sha256"]).lower()
    if stat.st_size != int(model["size_bytes"]):
        raise ModelIntegrityError("Размер модели не совпадает с реестром. Файл не активирован.")
    if magic != b"GGUF":
        raise ModelIntegrityError("Файл не является GGUF. Он не будет загружен в runtime.")
    digest = sha256_file(path)
    if digest.lower() != expected:
        raise ModelIntegrityError("SHA-256 модели не совпадает с закреплённым значением.")


def installed(model: dict[str, Any], root: Path | None = None) -> bool:
    if not model.get("filename") or model.get("size_bytes") is None or not model.get("sha256"):
        return False
    path = model_path(model, root)
    if model.get("custom"):
        try:
            return path.is_file() and path.stat().st_size == int(model["size_bytes"])
        except (OSError, ValueError, TypeError):
            return False
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
