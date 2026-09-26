from __future__ import annotations

import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotlingo.models import ModelIntegrityError, model_path, sha256_file, verify_model


class DownloadCancelled(RuntimeError):
    pass


class ModelDownloadError(RuntimeError):
    pass


def _trusted_https(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (host == "huggingface.co" or host.endswith(".huggingface.co") or host.endswith(".hf.co"))


def _safe_file(parent: Path, name: str) -> Path:
    if Path(name).name != name or any(c in name for c in ("/", "\\", ":", "\x00")):
        raise ModelDownloadError("В реестре модели указан небезопасный путь.")
    parent.mkdir(parents=True, exist_ok=True)
    root = parent.resolve()
    candidate = parent / name
    if candidate.is_symlink() or not candidate.resolve(strict=False).is_relative_to(root):
        raise ModelDownloadError("Путь модели выходит за пределы каталога моделей.")
    return candidate


def download_model(
    model: dict[str, Any],
    root: Path,
    *,
    cancel: Any,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    timeout: float = 30,
) -> Path:
    """Download one consented, pinned file, verify it, then atomically activate it."""
    if model.get("status") != "available":
        raise ModelDownloadError("Эта модель пока не прошла проверку runtime.")
    if not re.fullmatch(r"[0-9a-f]{40}", model["revision"]):
        raise ModelDownloadError("Для модели не закреплена полная ревизия.")
    size = int(model["size_bytes"])
    if size <= 0 or len(model.get("sha256", "")) != 64:
        raise ModelDownloadError("В реестре отсутствует проверяемый размер или SHA-256.")

    model_dir = Path(root) / str(model["id"])
    target = model_path(model, Path(root))
    _safe_file(model_dir, model["filename"])
    if target.exists():
        try:
            verify_model(target, model)
            return target
        except ModelIntegrityError as exc:
            raise ModelDownloadError("Файл с таким именем уже есть, но не совпадает с реестром. Переименуйте его вручную и повторите загрузку.") from exc
    url = (
        f"https://huggingface.co/{model['repo']}/resolve/"
        f"{model['revision']}/{urllib.parse.quote(model['filename'])}"
    )
    if not _trusted_https(url):
        raise ModelDownloadError("Источник модели отсутствует в списке доверенных HTTPS-доменов.")
    headers = {"User-Agent": "DotLingo/0.1 model downloader", "Accept-Encoding": "identity"}
    stable_partial = _safe_file(model_dir, f"{model['filename']}.part")
    candidates = [stable_partial] if stable_partial.exists() else []
    extras = [
        _safe_file(model_dir, item.name)
        for item in model_dir.glob(f"{model['filename']}.*.part")
        if item != stable_partial
    ]
    candidates.extend(sorted(extras, key=lambda item: item.stat().st_mtime, reverse=True))
    partial: Path | None = None
    done = 0
    for candidate in candidates:
        _safe_file(model_dir, candidate.name)
        candidate_size = candidate.stat().st_size
        if candidate_size > size:
            continue
        if candidate_size == size:
            digest = sha256_file(candidate)
            if digest.lower() == model["sha256"].lower():
                with candidate.open("rb") as model_file:
                    if model_file.read(4) != b"GGUF":
                        continue
                try:
                    os.rename(candidate, target)
                except FileExistsError as exc:
                    raise ModelDownloadError("Модель уже активирована другим процессом.") from exc
                _write_installed_marker(target, model, digest)
                return target
            continue
        partial = candidate
        done = candidate_size
        break
    if partial is None:
        partial = stable_partial if not stable_partial.exists() else _safe_file(
            model_dir, f"{model['filename']}.{uuid.uuid4().hex}.part"
        )
    try:
        free = shutil.disk_usage(model_dir).free
    except OSError as exc:
        raise ModelDownloadError("Не удалось определить свободное место на диске модели.") from exc
    needed = size - done + 256 * 1024 * 1024
    if free < needed:
        raise ModelDownloadError(f"Для оставшихся байтов и запаса нужно ещё около {needed / 1024**3:.1f} ГБ свободного места.")
    append_partial = partial.exists()
    resumed_from = done
    started = time.monotonic()
    if done:
        headers["Range"] = f"bytes={done}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if not _trusted_https(response.geturl()):
                raise ModelDownloadError("Перенаправление модели ведёт на неизвестный домен.")
            status_code = getattr(response, "status", response.getcode())
            if resumed_from:
                content_range = response.headers.get("Content-Range", "")
                if status_code != 206 or not content_range.startswith(f"bytes {resumed_from}-"):
                    raise ModelDownloadError("Сервер не подтвердил продолжение загрузки. Временный файл сохранён; повторите позже.")
            elif status_code != 200:
                raise ModelDownloadError("Сервер вернул неожиданный статус загрузки модели.")
            response_size = response.headers.get("Content-Length")
            expected = int(response_size) if response_size and response_size.isdigit() else size - resumed_from
            if expected != size - resumed_from:
                raise ModelDownloadError("Сервер сообщил размер файла, отличный от закреплённого в каталоге.")
            with partial.open("ab" if append_partial else "xb") as output:
                while True:
                    if cancel.is_set():
                        output.flush()
                        os.fsync(output.fileno())
                        raise DownloadCancelled("Загрузка отменена. Повторный запуск продолжит с уже полученных байтов.")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(
                            {
                                "bytes": done,
                                "total": size,
                                "ratio": min(1.0, done / size),
                                "speed_bps": (done - resumed_from) / max(time.monotonic() - started, 0.01),
                            }
                        )
                output.flush()
                os.fsync(output.fileno())
    except DownloadCancelled:
        raise
    except urllib.error.URLError as exc:
        raise ModelDownloadError("Сеть оборвалась. Можно повторить загрузку; этот временный файл останется на диске.") from exc
    except (OSError, TimeoutError) as exc:
        raise ModelDownloadError("Не удалось загрузить модель. Проверьте сеть и свободное место.") from exc

    if done != size:
        raise ModelDownloadError(f"Загрузка прервалась: получено {done} байт из {size}. Временный файл сохранён.")
    digest = sha256_file(partial)
    if digest.lower() != model["sha256"].lower():
        raise ModelIntegrityError("SHA-256 загрузки не совпал. Вес оставлен во временном файле и не активирован.")
    with partial.open("rb") as model_file:
        if model_file.read(4) != b"GGUF":
            raise ModelIntegrityError("Загруженный файл не прошёл проверку заголовка GGUF.")
    try:
        os.rename(partial, target)
    except FileExistsError as exc:
        raise ModelDownloadError("Модель уже активирована другим процессом; новая загрузка оставлена во временном файле.") from exc
    _write_installed_marker(target, model, digest)
    if on_progress:
        on_progress({"bytes": size, "total": size, "ratio": 1.0, "complete": True})
    return target


def _write_installed_marker(target: Path, model: dict[str, Any], digest: str) -> None:
    marker = target.parent / "installed.json"
    marker_tmp = target.parent / f"installed.{uuid.uuid4().hex}.tmp"
    marker_tmp.write_text(
        json.dumps(
            {
                "id": model["id"],
                "revision": model["revision"],
                "sha256": digest,
                "size_bytes": int(model["size_bytes"]),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    os.replace(marker_tmp, marker)
