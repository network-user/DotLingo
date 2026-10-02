"""Локальные диалоги без проекта. Оригиналы файлов не изменяются."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotlingo.formats import DocumentError, import_document

_ID = re.compile(r"^[0-9a-f]{32}$")
ATTACH_CHARS = 8000
MESSAGE_CAP = 80
TEXT_CAP = 8000


def _folder(root: Path) -> Path:
    return Path(root) / "dialogs"


def _safe_id(value: str) -> str:
    dialog_id = str(value or "").strip().lower()
    if not _ID.fullmatch(dialog_id):
        raise ValueError("Некорректный диалог.")
    return dialog_id


def _path(root: Path, dialog_id: str) -> Path:
    return _folder(root) / f"{_safe_id(dialog_id)}.json"


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f"{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temp = Path(handle.name)
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    os.replace(temp, path)


def _clean_message(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    role = raw.get("role")
    if role not in ("user", "model"):
        return None
    text = str(raw.get("text") or "")[:TEXT_CAP]
    item: dict[str, Any] = {
        "id": str(raw.get("id") or "")[:40],
        "role": role,
        "text": text,
    }
    if raw.get("error"):
        item["error"] = str(raw.get("error"))[:500]
    return item


def _clean(raw: dict[str, Any]) -> dict[str, Any]:
    dialog_id = _safe_id(str(raw.get("id") or ""))
    messages = []
    for item in raw.get("messages") or []:
        cleaned = _clean_message(item)
        if cleaned is not None:
            messages.append(cleaned)
    messages = messages[-MESSAGE_CAP:]
    title = str(raw.get("title") or "").strip()[:80] or "Новый диалог"
    mode = raw.get("mode") if raw.get("mode") in ("translate", "ask") else "translate"
    updated = str(raw.get("updatedAt") or "")
    if not updated:
        updated = datetime.now(timezone.utc).isoformat()
    return {
        "id": dialog_id,
        "title": title,
        "updatedAt": updated,
        "mode": mode,
        "modelId": str(raw.get("modelId") or "")[:80],
        "sourceLang": str(raw.get("sourceLang") or "auto")[:16],
        "targetLang": str(raw.get("targetLang") or "")[:16],
        "context": str(raw.get("context") or "")[:800],
        "messages": messages,
    }


def list_dialogs(root: Path) -> list[dict[str, Any]]:
    folder = _folder(root)
    if not folder.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for path in folder.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            dialog = _clean(data)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        found.append(
            {
                "id": dialog["id"],
                "title": dialog["title"],
                "updatedAt": dialog["updatedAt"],
                "mode": dialog["mode"],
                "messages": len(dialog["messages"]),
            }
        )
    found.sort(key=lambda item: item["updatedAt"], reverse=True)
    return found[:40]


def load_dialog(root: Path, dialog_id: str) -> dict[str, Any] | None:
    path = _path(root, dialog_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return _clean(data)


def save_dialog(root: Path, raw: dict[str, Any]) -> dict[str, Any]:
    dialog = _clean(raw)
    dialog["updatedAt"] = datetime.now(timezone.utc).isoformat()
    _write(_path(root, dialog["id"]), dialog)
    return dialog


def delete_dialog(root: Path, dialog_id: str) -> None:
    path = _path(root, dialog_id)
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ValueError("Не удалось удалить диалог.") from exc


def read_attachment(path: Path) -> dict[str, Any]:
    """Текст вложения для одной реплики. Файл на диске не меняется."""
    source = Path(path)
    if not source.is_file():
        raise ValueError("Файл не найден.")
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise ValueError("Не удалось прочитать файл.") from exc
    if size > 8 * 1024 * 1024:
        raise ValueError("Файл больше 8 МБ. Длинный текст лучше положить в проект.")
    try:
        parsed = import_document(source)
    except DocumentError as exc:
        raise ValueError(str(exc)) from exc
    text = "\n\n".join(block.text.strip() for block in parsed.blocks if block.text and block.text.strip())
    clipped = text[:ATTACH_CHARS]
    return {
        "name": source.name[:180],
        "text": clipped,
        "truncated": len(text) > ATTACH_CHARS,
        "chars": len(clipped),
    }
