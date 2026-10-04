"""pywebview bridge: a facade between the web UI and the DotLingo core."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import webview
from webview.dom import _dnd_state

from dotlingo.dialogs import delete_dialog, list_dialogs, load_dialog, read_attachment, save_dialog
from dotlingo.formats import export_document
from dotlingo.hardware import (
    HardwareSnapshot,
    assess_model,
    detect,
    gpu_layers_for,
    plan_setup,
    recommend_model,
)
from dotlingo.languages import (
    AUTO_LANGUAGE,
    AUTO_LANGUAGE_LABEL,
    LANGUAGES,
    language_label,
    supported_languages,
    supports_language,
)
from dotlingo.locale_hints import interface_language, keyboard_language
from dotlingo.market import (
    MarketError,
    confirm_offer,
    find_cached_offer,
    load_market_cache,
    match_market_link,
    refresh_market,
    register_offer,
)
from dotlingo.model_download import DownloadCancellation, download_model
from dotlingo.models import (
    ModelIntegrityError,
    all_models,
    get_model,
    import_custom_model,
    installed,
    model_path,
    verify_model,
)
from dotlingo.paths import user_data_root
from dotlingo.preferences import load_preferences, save_preferences
from dotlingo.scratch import (
    SCRATCH_LIMIT,
    ScratchRejected,
    ScratchTranslator,
    load_model_for_scratch,
)
from dotlingo.storage import ProjectStore, delete_project, list_projects
from dotlingo.task_queue import TaskQueue, build_chunks

# Формат фильтра: «описание (*.ext)». Строка без описания pywebview отвергает,
# и диалог тогда не открывается. То же правило, что у GGUF_FILE_TYPES.
IMPORT_EXTENSIONS = (
    "Документы (*.txt;*.md;*.markdown;*.docx;*.epub;*.pdf)",
    "Все файлы (*.*)",
)


EXPORT_ALLOWED = {
    "txt": (".txt", ".md"),
    "markdown": (".md", ".txt"),
    "docx": (".docx", ".txt", ".md"),
    "epub": (".epub", ".txt", ".md"),
    "pdf": (".txt", ".md"),
}

# Быстрый перевод без выбранного проекта живёт в одном локальном проекте.
QUICK_PROJECT_TITLE = "Быстрые"

# Тот же контейнер, что у оригинала. Книжная укладка подменяет PDF на .pdf в publishTranslation.
# Обычный PDF без полосы книги остаётся Markdown.
SAME_FORMAT_SUFFIX = {
    "txt": ".txt",
    "markdown": ".md",
    "docx": ".docx",
    "epub": ".epub",
    "pdf": ".md",
}


def _layout_span(value: Any, low: int, high: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return min(high, max(low, number))


# Формат фильтра жёсткий: «описание (*.ext)». Дефис в описании pywebview отвергает,
# и диалог тогда не открывается.
GGUF_FILE_TYPES = ("GGUF (*.gguf)", "Все файлы (*.*)")


def _arm_webview_file_drop() -> None:
    """Разрешить WebView2 запомнить путь файла, который бросили в окно."""
    if int(_dnd_state.get("num_listeners") or 0) < 1:
        _dnd_state["num_listeners"] = 1


def _ok(data: Any = None) -> dict[str, Any]:
    return {"ok": True, "data": data}


def _err(message: str, code: str = "error") -> dict[str, Any]:
    return {"ok": False, "error": str(message), "code": code}


def _safe_stem(name: str) -> str:
    stem = Path(name).stem
    cleaned = "".join("-" if char in '<>:"/\\|?*' else char for char in stem).strip(" .")
    return cleaned[:80] or "translation"


def _write_translation(
    store: ProjectStore,
    doc_id: str,
    target_lang: str,
    destination: Path,
) -> None:
    store.verify_source(doc_id)
    translations = store.translations(doc_id, target_lang=target_lang)
    export_document(
        store.document(doc_id).source_path,
        destination,
        store.parsed(doc_id),
        translations,
    )
    try:
        store.record_export(doc_id, destination, target_lang=target_lang)
    except Exception:
        pass


def _public_offer(offer: dict[str, Any], installed_sha: bool) -> dict[str, Any]:
    return {
        "id": offer["id"],
        "name": offer.get("name") or offer["id"],
        "repo": offer.get("repo") or "",
        "revision": offer.get("revision") or "",
        "filename": offer.get("filename") or "",
        "quantization": offer.get("quantization") or "",
        "role": offer.get("role") or "",
        "roleLabel": offer.get("role_label") or "",
        "sizeBytes": offer.get("size_bytes"),
        "sizeLabel": _format_size(offer.get("size_bytes")),
        "license": offer.get("license") or "Не указана",
        "cardUrl": offer.get("card_url") or "",
        "summary": offer.get("ui_description") or "",
        "caveat": offer.get("ui_details") or "",
        "installed": installed_sha,
    }


def _format_size(size: int | float | None) -> str:
    if size is None:
        return "неизвестно"
    size = int(size)
    if size >= 1024**3:
        return f"{size / 1024**3:.2f} ГБ"
    return f"{size / 1024**2:.0f} МБ"


def _scratch_pairs(value: Any) -> list[tuple[str, str]]:
    """Последние две пары диалога как контекст тона, не как инструкция."""
    if not isinstance(value, list):
        return []
    pairs: list[tuple[str, str]] = []
    for item in value[-2:]:
        if not isinstance(item, dict):
            continue
        source = " ".join(str(item.get("source") or "").split())[:220]
        translation = " ".join(str(item.get("translation") or "").split())[:220]
        if source and translation:
            pairs.append((source, translation))
    return pairs


def _runtime_available() -> bool:
    # Импорт llama_cpp сам лезет в CUDA_PATH\\bin. Пустой каталог v12.6 даёт WinError 3.
    from dotlingo.engine import prepare_llama_env

    prepare_llama_env()
    try:
        import llama_cpp  # noqa: F401
    except ImportError:
        return False
    except OSError:
        sys.modules.pop("llama_cpp", None)
        return False
    return True


SPECTRUM_TICKS = 160

HARDWARE_CACHE = "hardware.json"


def _serialize_hardware(
    snapshot: HardwareSnapshot, detected_at: str | None = None
) -> dict[str, Any]:
    return {
        "profile": snapshot.profile,
        "cpuThreads": snapshot.cpu_threads,
        "ramTotalGb": snapshot.ram_total_gb,
        "ramAvailableGb": snapshot.ram_available_gb,
        "diskFreeGb": snapshot.disk_free_gb,
        "gpuNames": list(snapshot.gpu_names or ()),
        "gpuVramGb": [value if value is not None else None for value in (snapshot.gpu_vram_gb or ())],
        "gpuVramFreeGb": [
            value if value is not None else None for value in (snapshot.gpu_vram_free_gb or ())
        ],
        "llamaRuntimeAvailable": snapshot.llama_runtime_available,
        "llamaGpuOffloadAvailable": snapshot.llama_gpu_offload_available,
        "detectedAt": detected_at or datetime.now(timezone.utc).isoformat(),
    }


def _hardware_from_cache(data: dict[str, Any]) -> HardwareSnapshot:
    return HardwareSnapshot(
        cpu_threads=int(data["cpuThreads"]),
        ram_total_gb=float(data["ramTotalGb"]),
        ram_available_gb=float(data["ramAvailableGb"]),
        disk_free_gb=float(data["diskFreeGb"]),
        gpu_names=tuple(data.get("gpuNames") or ()),
        gpu_vram_gb=tuple(data.get("gpuVramGb") or ()),
        llama_runtime_available=bool(data.get("llamaRuntimeAvailable")),
        gpu_vram_free_gb=(
            None if data.get("gpuVramFreeGb") is None else tuple(data.get("gpuVramFreeGb") or ())
        ),
        llama_gpu_offload_available=data.get("llamaGpuOffloadAvailable"),
    )


def _load_hardware_cache(root: Path) -> tuple[HardwareSnapshot | None, str | None]:
    """Прочитать кэш последней проверки устройства; молча при любой порче."""
    try:
        data = json.loads((Path(root) / HARDWARE_CACHE).read_text(encoding="utf-8"))
        snapshot = _hardware_from_cache(data)
    except (OSError, ValueError, KeyError, TypeError):
        return None, None
    detected_at = data.get("detectedAt")
    return snapshot, detected_at if isinstance(detected_at, str) else None


def _save_hardware_cache(root: Path, snapshot: HardwareSnapshot, detected_at: str) -> None:
    """Атомарно записать кэш проверки устройства."""
    path = Path(root) / HARDWARE_CACHE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(_serialize_hardware(snapshot, detected_at), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, path)
    except OSError:
        # Кэш не критичен: без него проверка просто выполнится заново.
        pass


def _block_spectrum(blocks: list[Any], translations: dict[int, str]) -> list[float]:
    """Спектр перевода: одно значение на блок (или корзину блоков).

    -1.0 - нетранслируемый блок, 0.0 - не переведён, 1.0 - переведён;
    для длинных документоов значения усредняются в корзины (не более SPECTRUM_TICKS).
    """
    flags: list[float] = []
    for block in blocks:
        if not getattr(block, "translatable", False):
            flags.append(-1.0)
        else:
            flags.append(1.0 if (translations.get(block.order) or "").strip() else 0.0)
    if len(flags) <= SPECTRUM_TICKS:
        return flags
    bucket = -(-len(flags) // SPECTRUM_TICKS)
    result: list[float] = []
    for start in range(0, len(flags), bucket):
        chunk = flags[start : start + bucket]
        meaningful = [value for value in chunk if value >= 0]
        if not meaningful:
            result.append(-1.0)
        else:
            result.append(round(sum(meaningful) / len(meaningful), 2))
    return result


def _serialize_project(store: ProjectStore, *, with_counts: bool = False) -> dict[str, Any]:
    project = store.project
    data = {
        "id": project["id"],
        "title": project.get("title") or "Проект",
        "sourceLang": project.get("source_lang") or AUTO_LANGUAGE,
        "targetLangs": store.target_languages(),
        "modelId": project.get("model_id") or "",
        "updatedAt": project.get("updated_at"),
        "context": project.get("context") or "",
        "rules": project.get("rules") or "",
    }
    if with_counts:
        documents = store.documents()
        data["documentCount"] = len(documents)
        counts: dict[str, int] = {}
        for task in store.tasks():
            counts[task["status"]] = counts.get(task["status"], 0) + 1
        data["taskSummary"] = counts
    return data


class Api:
    """js_api facade exposed to the web layer; safe to call from any thread."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else user_data_root()
        self.projects_dir = self.data_dir / "projects"
        self.models_dir = self.data_dir / "models"
        self.preferences_root = self.data_dir
        self.projects: list[ProjectStore] = list_projects(self.projects_dir)
        self.project_queues: dict[str, TaskQueue] = {}
        self.active_project_id: str | None = None
        self._lock = threading.RLock()
        self._window: Any = None
        self._workers: list[threading.Thread] = []
        self._download_cancel: DownloadCancellation | None = None
        self._market_busy = False
        self._closing = False
        self._close_permitted = False
        self._close_animating = False
        self._window_closer: Callable[[], None] | None = None
        self.hardware, self._hardware_detected_at = _load_hardware_cache(self.data_dir)
        self._scratch = ScratchTranslator(self.models_dir, self._push, self._document_busy)
        _arm_webview_file_drop()
        preferences = load_preferences(self.preferences_root)
        self._preferences = preferences
        last = preferences.get("last_project") or ""
        if last and any(project.project["id"] == last for project in self.projects):
            self.active_project_id = last

    # ------------------------------------------------------------------ plumbing

    def attach_window(self, window: Any) -> None:
        """Bind the pywebview window so the bridge can push events and dialogs."""
        self._window = window

    def set_window_closer(self, closer: Callable[[], None] | None) -> None:
        """Куда звать, когда книга уже закрылась и окно можно отпустить."""
        self._window_closer = closer

    def note_close_attempt(self) -> str:
        """allow - окно можно закрыть, wait - книга уже складывается, animate - начать кадр."""
        if self._close_permitted:
            return "allow"
        if self._close_animating:
            return "wait"
        self._close_animating = True
        return "animate"

    def permit_close(self) -> None:
        self._close_permitted = True

    @property
    def close_permitted(self) -> bool:
        return self._close_permitted

    def _push(self, name: str, payload: dict[str, Any]) -> None:
        window = self._window
        if window is None:
            return
        try:
            window.evaluate_js(f"window.DL && window.DL.push_event({json.dumps(name)},{json.dumps(payload)})")
        except Exception:
            pass

    def _spawn(self, work: Callable[[], None], name: str) -> threading.Thread:
        worker = threading.Thread(target=work, name=f"DotLingo {name}", daemon=True)
        with self._lock:
            self._workers.append(worker)
        worker.start()
        return worker

    def _queue_for(self, store: ProjectStore) -> TaskQueue:
        key = str(store.root)
        queue = self.project_queues.get(key)
        if queue is None:
            queue = TaskQueue(
                store,
                self.models_dir,
                on_event=lambda event, s=store: self._on_task_event(s, event),
            )
            self.project_queues[key] = queue
        return queue

    def _document_busy(self) -> bool:
        return any(queue.busy for queue in self.project_queues.values())

    def _on_task_event(self, store: ProjectStore, event: dict[str, Any]) -> None:
        task = None
        try:
            task = store.task(event.get("task_id", ""))
        except KeyError:
            pass
        document_name = str(event.get("document_name") or "")
        if not document_name and task is not None:
            try:
                document_name = store.document(task["document_id"]).name
            except KeyError:
                document_name = ""
        self._push(
            "task_event",
            {
                "projectId": store.project["id"],
                "documentName": document_name,
                "task": event,
            },
        )

    def _sync_projects(self) -> None:
        """Подхватить новые каталоги, не переоткрывая уже загруженные базы."""
        base = self.projects_dir
        if not base.is_dir():
            self.projects = []
            return
        found: dict[str, Path] = {}
        for child in base.iterdir():
            if child.is_dir() and (child / "project.sqlite").is_file():
                found[child.name] = child
        current = {store.root.name: store for store in self.projects}
        if set(found) == set(current):
            return
        stores: list[ProjectStore] = []
        for name, path in found.items():
            store = current.get(name)
            if store is None:
                try:
                    store = ProjectStore(path)
                except (OSError, sqlite3.DatabaseError):
                    continue
            stores.append(store)
        self.projects = stores

    def _active_store(self) -> ProjectStore | None:
        for project in self.projects:
            if project.project["id"] == self.active_project_id:
                return project
        return None

    def _store_by_id(self, project_id: str) -> ProjectStore | None:
        for project in self.projects:
            if project.project["id"] == project_id:
                return project
        return None

    def _set_preference(self, key: str, value: Any) -> None:
        self._preferences[key] = value
        save_preferences(self._preferences, self.preferences_root)

    # ------------------------------------------------------------------ preferences

    def getPreferences(self) -> dict[str, Any]:
        return _ok(dict(self._preferences))

    def listLanguages(self) -> dict[str, Any]:
        return _ok(
            {
                "auto": {"code": AUTO_LANGUAGE, "label": AUTO_LANGUAGE_LABEL},
                "languages": [{"code": code, "label": label} for code, label in LANGUAGES.items()],
            }
        )

    def setPreferences(self, prefs: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(prefs, dict):
            return _err("Некорректные настройки.")
        for key in ("theme", "reduce_motion", "last_project", "setup_seen"):
            if key in prefs:
                self._set_preference(key, prefs[key])
        if "sidebar_collapsed" in prefs:
            self._set_preference("sidebar_collapsed", bool(prefs["sidebar_collapsed"]))
        if "sidebar_width" in prefs:
            self._set_preference(
                "sidebar_width",
                _layout_span(prefs["sidebar_width"], 80, 480, 252),
            )
        if "chat_list_width" in prefs:
            self._set_preference(
                "chat_list_width",
                _layout_span(prefs["chat_list_width"], 168, 1200, 240),
            )
        if "chat_list_height" in prefs:
            self._set_preference(
                "chat_list_height",
                _layout_span(prefs["chat_list_height"], 120, 800, 200),
            )
        if "chat_list_hidden" in prefs:
            self._set_preference("chat_list_hidden", bool(prefs["chat_list_hidden"]))
        if "ui_scale" in prefs:
            self._set_preference("ui_scale", _layout_span(prefs["ui_scale"], 75, 160, 100))
        if "text_scale" in prefs:
            self._set_preference("text_scale", _layout_span(prefs["text_scale"], 75, 180, 100))
        return _ok(dict(self._preferences))

    # ------------------------------------------------------------------ projects

    def listProjects(self) -> dict[str, Any]:
        self._sync_projects()
        items = [_serialize_project(store, with_counts=True) for store in self.projects]
        items.sort(key=lambda item: item.get("updatedAt") or "", reverse=True)
        return _ok(items)

    def createProject(self, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            return _err("Некорректные данные проекта.")
        title = str(data.get("title") or "").strip() or "Новый проект"
        model_id = str(data.get("modelId") or "").strip()
        source_lang = str(data.get("sourceLang") or AUTO_LANGUAGE).strip() or AUTO_LANGUAGE
        raw_targets = data.get("targetLangs") or []
        targets = [str(item) for item in raw_targets if str(item).strip()]
        if not targets:
            return _err("Выберите хотя бы один целевой язык.", "no_targets")
        if model_id:
            try:
                model = get_model(model_id, root=self.models_dir)
            except KeyError:
                return _err("Выбранная модель не найдена.", "model_missing")
            for code in targets:
                if not supports_language(model, code):
                    return _err(
                        f"Модель не перечисляет поддержку: {language_label(code)}.",
                        "language_unsupported",
                    )
            if source_lang != AUTO_LANGUAGE and not supports_language(model, source_lang):
                return _err(
                    f"Модель не перечисляет поддержку: {language_label(source_lang)}.",
                    "language_unsupported",
                )
        try:
            store = ProjectStore.create(
                self.projects_dir,
                title,
                source_lang,
                targets[0],
                target_langs=targets,
                model_id=model_id,
            )
        except (OSError, ValueError) as exc:
            return _err(f"Не удалось создать проект: {exc}")
        with self._lock:
            self.projects = [store, *self.projects]
            self.active_project_id = store.project["id"]
        self._set_preference("last_project", store.project["id"])
        return _ok(_serialize_project(store, with_counts=True))

    def ensureWorkProject(self, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Открыть выбранный проект или один повторно используемый «Быстрые»."""
        payload = data if isinstance(data, dict) else {}
        project_id = str(payload.get("projectId") or "").strip()
        if project_id:
            return self.openProject(project_id)
        self._sync_projects()
        matches = [
            store
            for store in self.projects
            if (store.project.get("title") or "") == QUICK_PROJECT_TITLE
        ]
        if matches:
            matches.sort(key=lambda store: store.project.get("updated_at") or "", reverse=True)
            store = matches[0]
            with self._lock:
                self.active_project_id = store.project["id"]
            self._set_preference("last_project", store.project["id"])
            return _ok(_serialize_project(store, with_counts=True))
        targets = payload.get("targetLangs") or ["ru"]
        return self.createProject(
            {
                "title": QUICK_PROJECT_TITLE,
                "modelId": payload.get("modelId") or "",
                "sourceLang": payload.get("sourceLang") or AUTO_LANGUAGE,
                "targetLangs": targets,
            }
        )

    def openProject(self, project_id: str) -> dict[str, Any]:
        store = self._store_by_id(project_id)
        if store is None:
            return _err("Проект не найден.", "not_found")
        with self._lock:
            self.active_project_id = project_id
        self._set_preference("last_project", project_id)
        return _ok(_serialize_project(store))

    def getActiveProject(self) -> dict[str, Any]:
        store = self._active_store()
        if store is None:
            return _ok(None)
        return _ok(_serialize_project(store, with_counts=True))

    def renameProject(self, project_id: str, title: str) -> dict[str, Any]:
        store = self._store_by_id(project_id)
        if store is None:
            return _err("Проект не найден.", "not_found")
        clean = str(title or "").strip()
        if not clean:
            return _err("Название проекта не может быть пустым.", "empty_title")
        store.update_settings(title=clean)
        return _ok(_serialize_project(store))

    def deleteProject(self, project_id: str) -> dict[str, Any]:
        store = self._store_by_id(project_id)
        if store is None:
            return _err("Проект не найден.", "not_found")
        key = str(store.root)
        queue = self.project_queues.pop(key, None)
        if queue is not None:
            queue.close(timeout=1)
        with self._lock:
            self.projects = [p for p in self.projects if p.project["id"] != project_id]
            if self.active_project_id == project_id:
                self.active_project_id = None
        try:
            delete_project(self.projects_dir, project_id)
        except (OSError, ValueError) as exc:
            return _err(f"Не удалось удалить проект: {exc}")
        return _ok(None)

    def updateProjectSettings(self, data: dict[str, Any]) -> dict[str, Any]:
        store = self._active_store()
        if store is None:
            return _err("Сначала выберите проект.", "no_project")
        if not isinstance(data, dict):
            return _err("Некорректные настройки.")
        model_id = data.get("modelId")
        source_lang = data.get("sourceLang")
        raw_targets = data.get("targetLangs") or []
        targets = [str(item) for item in raw_targets if str(item).strip()]
        if not targets:
            return _err("Выберите хотя бы один целевой язык.", "no_targets")
        if source_lang and source_lang != AUTO_LANGUAGE and source_lang in targets:
            return _err("Исходный язык не может совпадать с целевым.", "same_language")
        if model_id:
            try:
                model = get_model(str(model_id), root=self.models_dir)
            except KeyError:
                return _err("Выбранная модель не найдена.", "model_missing")
            codes = [c for c in [*targets, source_lang or AUTO_LANGUAGE] if c != AUTO_LANGUAGE]
            unsupported = [c for c in codes if not supports_language(model, c)]
            if unsupported:
                labels = ", ".join(language_label(c) for c in dict.fromkeys(unsupported))
                return _err(
                    f"Модель не перечисляет поддержку: {labels}. Измените модель или языки проекта.",
                    "language_unsupported",
                )
        store.update_settings(
            model_id=str(model_id) if model_id is not None else None,
            source_lang=str(source_lang) if source_lang is not None else None,
            target_langs=targets,
            context=str(data.get("context") or ""),
            rules=str(data.get("rules") or ""),
        )
        return _ok(_serialize_project(store, with_counts=True))

    # ------------------------------------------------------------------ documents

    def importDocuments(self, paths: list[str]) -> dict[str, Any]:
        store = self._active_store()
        if store is None:
            return _err("Сначала выберите проект.", "no_project")
        if not paths:
            return _ok({"imported": [], "errors": []})

        def work() -> None:
            imported: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []
            for raw in paths:
                path = Path(raw)
                try:
                    record = store.import_file(path)
                    imported.append(
                        {
                            "id": record.id,
                            "name": record.name,
                            "format": record.format,
                            "warnings": list(record.warnings),
                        }
                    )
                except Exception as exc:
                    errors.append({"name": path.name, "error": str(exc)})
            self._push("documents_imported", {"ok": not errors, "imported": imported, "errors": errors})

        self._spawn(work, "import")
        return _ok({"started": True})

    def listDocuments(self) -> dict[str, Any]:
        store = self._active_store()
        if store is None:
            return _err("Сначала выберите проект.", "no_project")
        targets = store.target_languages()
        result: list[dict[str, Any]] = []
        for item in store.document_listing():
            progress_by_target: dict[str, dict[str, int]] = {}
            spectrum_by_target: dict[str, list[float]] = {}
            done_by_lang = item["doneByLang"]
            blocks = item["blocks"]
            total_translatable = sum(1 for block in blocks if block.translatable)
            for lang in targets:
                done_orders = done_by_lang.get(lang, set())
                done = sum(
                    1 for block in blocks if block.translatable and block.order in done_orders
                )
                progress_by_target[lang] = {"done": done, "total": total_translatable}
                spectrum_by_target[lang] = _block_spectrum(
                    blocks, {order: "1" for order in done_orders}
                )
            result.append(
                {
                    "id": item["id"],
                    "name": item["name"],
                    "format": item["format"],
                    "warnings": item["warnings"],
                    "progressByTarget": progress_by_target,
                    "spectrumByTarget": spectrum_by_target,
                    "exportCount": item["exportCount"],
                    "detectedLanguage": item["detectedLanguage"],
                }
            )
        return _ok(result)

    def getDocument(self, doc_id: str) -> dict[str, Any]:
        store = self._active_store()
        if store is None:
            return _err("Сначала выберите проект.", "no_project")
        try:
            record = store.document(doc_id)
        except KeyError:
            return _err("Документ не найден.", "not_found")
        parsed = store.parsed(doc_id)
        blocks = [
            {
                "order": block.order,
                "section": block.section,
                "sectionTitle": block.section_title,
                "kind": block.kind,
                "text": block.text,
                "translatable": block.translatable,
            }
            for block in parsed.blocks
        ]
        translations: dict[str, dict[str, str]] = {}
        edited_flags: dict[str, dict[str, bool]] = {}
        machine_drafts: dict[str, dict[str, str]] = {}
        for lang in store.target_languages():
            translations[lang] = {
                str(order): text
                for order, text in store.translations(doc_id, target_lang=lang).items()
            }
            edited_flags[lang] = {
                str(order): flag
                for order, flag in store.translation_flags(doc_id, lang).items()
            }
            machine_drafts[lang] = {
                str(order): text
                for order, text in store.machine_draft(doc_id, lang).items()
            }
        exports = [
            {
                "path": item["path"],
                "format": item.get("format") or "",
                "targetLang": item.get("target_lang") or "",
                "createdAt": item.get("created_at"),
            }
            for item in store.exports(doc_id)
        ]
        return _ok(
            {
                "id": record.id,
                "name": record.name,
                "format": record.format,
                "warnings": list(record.warnings),
                "blocks": blocks,
                "translations": translations,
                "editedFlags": edited_flags,
                "machineDrafts": machine_drafts,
                "detectedLanguage": store.detected_language(doc_id),
                "exports": exports,
            }
        )

    def saveEdit(self, doc_id: str, block_order: int, text: str, target_lang: str) -> dict[str, Any]:
        store = self._active_store()
        if store is None:
            return _err("Сначала выберите проект.", "no_project")
        try:
            store.save_edit(doc_id, int(block_order), str(text), target_lang=str(target_lang))
        except (KeyError, ValueError) as exc:
            return _err(f"Не удалось сохранить правку: {exc}")
        return _ok(None)

    def exportDocument(self, doc_id: str, target_lang: str, dest_path: str) -> dict[str, Any]:
        store = self._active_store()
        if store is None:
            return _err("Сначала выберите проект.", "no_project")
        destination = Path(dest_path)
        try:
            _write_translation(store, str(doc_id), str(target_lang), destination)
        except Exception as exc:
            return _err(f"Не удалось экспортировать: {exc}", "export_failed")
        self._push("export_done", {"ok": True, "path": str(destination), "error": None})
        return _ok({"path": str(destination)})

    def publishTranslation(
        self,
        doc_id: str,
        target_lang: str,
        project_id: str = "",
    ) -> dict[str, Any]:
        """Собрать перевод в каталог output проекта, не трогая оригинал."""
        if project_id:
            store = self._store_by_id(str(project_id))
        else:
            store = self._active_store()
        if store is None:
            return _err("Сначала выберите проект.", "no_project")
        try:
            record = store.document(str(doc_id))
            parsed = store.parsed(str(doc_id))
        except KeyError:
            return _err("Документ не найден.", "not_found")
        lang = str(target_lang or "").strip()
        if not lang:
            return _err("Не выбран язык перевода.", "no_target")
        layout = parsed.metadata.get("pdfLayout") == "book"
        suffix = ".pdf" if layout else SAME_FORMAT_SUFFIX.get(record.format, ".txt")
        note = ""
        if record.format == "pdf" and not layout:
            note = "PDF возвращается как Markdown: запись PDF в PDF в программе нет."
        for item in store.exports(str(doc_id)):
            if str(item.get("target_lang") or "") != lang:
                continue
            existing = Path(str(item.get("path") or ""))
            if existing.is_file() and existing.suffix.lower() == suffix:
                return _ok(
                    {
                        "path": str(existing),
                        "format": record.format,
                        "suffix": suffix,
                        "note": note,
                        "reused": True,
                    }
                )
        destination = store.root / "output" / f"{_safe_stem(record.name)}.{lang}{suffix}"
        try:
            _write_translation(store, str(doc_id), lang, destination)
        except Exception as exc:
            return _err(f"Не удалось собрать файл: {exc}", "export_failed")
        self._push("export_done", {"ok": True, "path": str(destination), "error": None})
        return _ok(
            {
                "path": str(destination),
                "format": record.format,
                "suffix": suffix,
                "note": note,
                "reused": False,
            }
        )

    # ------------------------------------------------------------------ translation

    def enqueueTranslation(self, data: dict[str, Any]) -> dict[str, Any]:
        store = self._active_store()
        if store is None:
            return _err("Сначала выберите проект.", "no_project")
        if not isinstance(data, dict):
            return _err("Некорректный запрос перевода.")
        doc_ids = [str(item) for item in (data.get("documentIds") or [])]
        targets = [str(item) for item in (data.get("targetLangs") or [])]
        if not doc_ids:
            return _err("Выберите один или несколько документов.", "no_documents")
        if not targets:
            return _err("У проекта нет целевых языков.", "no_targets")
        model_id = store.project.get("model_id") or ""
        if not model_id:
            return _err("Сначала выберите модель в настройках проекта.", "model_missing")
        try:
            model = get_model(model_id, root=self.models_dir)
        except KeyError:
            return _err("Модель проекта не найдена в каталоге.", "model_missing")
        if not installed(model, self.models_dir):
            return _err("Сначала установите выбранную модель на странице «Модели».", "model_missing")
        if not _runtime_available():
            return _err(
                "В этой сборке не найден llama-cpp-python. Установка веса модели не добавляет runtime. "
                "См. docs/BUILD_WINDOWS.md.",
                "runtime_missing",
            )
        source = store.project.get("source_lang") or AUTO_LANGUAGE
        unsupported = [target for target in targets if not supports_language(model, target)]
        if source != AUTO_LANGUAGE and not supports_language(model, source):
            unsupported.insert(0, source)
        if unsupported:
            labels = ", ".join(language_label(code) for code in dict.fromkeys(unsupported))
            return _err(
                f"Выбранная модель не перечисляет поддержку: {labels}. Измените модель или языки проекта.",
                "language_unsupported",
            )
        documents = []
        for doc_id in dict.fromkeys(doc_ids):
            try:
                documents.append(store.document(doc_id))
            except KeyError:
                return _err("Документ не найден.", "not_found")
        task_ids: list[str] = []
        warnings: list[dict[str, Any]] = []
        # Диалог держит ту же модель в памяти. Перед документом её нужно отпустить.
        self._scratch.close()
        try:
            queue = self._queue_for(store)
            for document in documents:
                chunks = build_chunks(store.blocks(document.id))
                for target in targets:
                    task_id = store.create_task(
                        document.id,
                        model_id,
                        chunks,
                        source_lang=source,
                        target_lang=target,
                    )
                    queue.enqueue(task_id)
                    task_ids.append(task_id)
                for warning in document.warnings:
                    item = {"document": document.name, "text": warning}
                    if item not in warnings:
                        warnings.append(item)
        except Exception as exc:
            return _err(f"Не удалось поставить задачу: {exc}")
        return _ok({"taskIds": task_ids, "warnings": warnings})

    def listTasks(self) -> dict[str, Any]:
        result: list[dict[str, Any]] = []
        self._sync_projects()
        for store in self.projects:
            title = store.project.get("title") or "Проект"
            documents = {record.id: record.name for record in store.documents()}
            for task in store.tasks():
                result.append(
                    {
                        "taskId": task["id"],
                        "projectId": store.project["id"],
                        "projectTitle": title,
                        "documentId": task["document_id"],
                        "documentName": documents.get(task["document_id"], ""),
                        "sourceLang": task.get("source_lang") or AUTO_LANGUAGE,
                        "targetLang": task.get("target_lang") or "",
                        "status": task["status"],
                        "completed": task.get("completed", 0),
                        "total": task.get("total", 0),
                        "error": task.get("error") or "",
                        "modelId": task.get("model_id") or "",
                    }
                )
        return _ok(result)

    def _task_queue_action(self, task_id: str, action: str) -> dict[str, Any]:
        for store in self.projects:
            try:
                task = store.task(task_id)
            except KeyError:
                continue
            if action == "resume" and task["status"] == "complete":
                return _err(
                    "Перевод уже завершён. Чтобы перевести заново, выберите документ на странице «Перевод».",
                    "already_complete",
                )
            queue = self._queue_for(store)
            getattr(queue, action)(task_id)
            return _ok(None)
        return _err("Задача не найдена.", "not_found")

    def pauseTask(self, task_id: str) -> dict[str, Any]:
        return self._task_queue_action(task_id, "pause")

    def resumeTask(self, task_id: str) -> dict[str, Any]:
        return self._task_queue_action(task_id, "resume")

    def cancelTask(self, task_id: str) -> dict[str, Any]:
        return self._task_queue_action(task_id, "cancel")

    # ------------------------------------------------------------------ models

    def listModels(self) -> dict[str, Any]:
        models = all_models(root=self.models_dir)
        installed_ids = {model["id"] for model in models if installed(model, self.models_dir)}
        recommendation = None
        if self.hardware is not None:
            found, reason = recommend_model(self.hardware, models, installed_ids)
            recommendation = {"id": found["id"], "reason": reason} if found else {"id": None, "reason": reason}
        result: list[dict[str, Any]] = []
        for model in models:
            entry = dict(model)
            is_installed = installed(model, self.models_dir)
            if model.get("custom"):
                state = "installed" if is_installed else "missing"
            elif is_installed:
                state = "installed"
            elif model.get("status") == "available":
                state = "available"
            else:
                state = "unverified"
            ram = model.get("estimated_ram_gb")
            compatibility = None
            if self.hardware is not None and ram is not None:
                verdict, reason = assess_model(self.hardware, model)
                compatibility = {"verdict": verdict, "reason": reason}
            result.append(
                {
                    "id": entry["id"],
                    "name": entry.get("name") or entry["id"],
                    "custom": bool(entry.get("custom")),
                    "status": entry.get("status") or "",
                    "installState": state,
                    "installed": is_installed,
                    "sizeBytes": entry.get("size_bytes"),
                    "sizeLabel": _format_size(entry.get("size_bytes")),
                    "estimatedRamGb": ram,
                    "license": entry.get("license") or "Не указана",
                    "licenseUrl": entry.get("license_url") or "",
                    "cardUrl": entry.get("card_url") or "",
                    "repo": entry.get("repo") or "",
                    "revision": entry.get("revision") or "",
                    "quantization": entry.get("quantization") or "",
                    "format": entry.get("format") or "",
                    "testedOnWindows": bool(entry.get("tested_on_windows")),
                    "languageCodes": list(supported_languages(model)),
                    "promptStyle": entry.get("prompt_style") or "general",
                    "uiDescription": entry.get("ui_description") or "",
                    "tier": entry.get("tier") or "",
                    "uiDetails": entry.get("ui_details") or "",
                    "notes": entry.get("notes") or "",
                    "compatibility": compatibility,
                }
            )
        return _ok({"models": result, "recommendation": recommendation})

    def planSetup(self) -> dict[str, Any]:
        """Один план первого запуска. Ошибка каталога не блокирует вход в приложение."""
        try:
            models = all_models(root=self.models_dir)
            installed_ids = {model["id"] for model in models if installed(model, self.models_dir)}
            plan = plan_setup(self.hardware, models, installed_ids)
            model = next((item for item in models if item.get("id") == plan["modelId"]), None)
        except Exception as exc:
            return _ok(
                {
                    "action": "skip",
                    "modelId": None,
                    "reason": f"Не удалось составить план: {exc}. Приложение откроется без загрузки.",
                    "name": "",
                    "sizeLabel": "",
                    "license": "",
                }
            )
        return _ok(
            {
                "action": plan["action"],
                "modelId": plan["modelId"],
                "reason": plan["reason"] or "",
                "name": (model or {}).get("name") or plan["modelId"] or "",
                "sizeLabel": _format_size(model.get("size_bytes")) if model else "",
                "license": (model or {}).get("license") or "",
            }
        )

    def getHardware(self) -> dict[str, Any]:
        if self.hardware is None:
            return _ok(None)
        return _ok(_serialize_hardware(self.hardware, self._hardware_detected_at))

    def getLanguages(self) -> dict[str, Any]:
        """Словарь код → русское название для UI."""
        return _ok(dict(LANGUAGES))

    def getLocaleHints(self) -> dict[str, Any]:
        """Раскладка и язык системы. Пустые строки значат, что сигнала нет."""
        return _ok({
            "keyboard": keyboard_language(),
            "interface": interface_language(),
        })

    def detectHardware(self) -> dict[str, Any]:
        """Локальная проверка устройства; результат кэшируется до явного повтора."""

        def work() -> None:
            snapshot = detect(self.models_dir)
            detected_at = datetime.now(timezone.utc).isoformat()
            with self._lock:
                self.hardware = snapshot
                self._hardware_detected_at = detected_at
            _save_hardware_cache(self.data_dir, snapshot, detected_at)
            self._push("hardware_detected", _serialize_hardware(snapshot, detected_at))

        self._spawn(work, "device_check")
        return _ok({"started": True})

    def verifyModel(self, model_id: str) -> dict[str, Any]:
        try:
            model = get_model(model_id, root=self.models_dir)
        except KeyError:
            return _err("Модель не найдена.", "not_found")
        if not installed(model, self.models_dir):
            return _err("Вес модели не установлен.", "not_installed")

        def work() -> None:
            try:
                verify_model(model_path(model, self.models_dir), model)
                self._push("model_verified", {"modelId": model_id, "ok": True, "error": None})
            except (ModelIntegrityError, OSError) as exc:
                self._push("model_verified", {"modelId": model_id, "ok": False, "error": str(exc)})

        self._spawn(work, "model verification")
        return _ok({"started": True})

    def downloadModel(self, model_id: str) -> dict[str, Any]:
        try:
            model = get_model(model_id, root=self.models_dir)
        except KeyError:
            return _err("Модель не найдена.", "not_found")
        if model.get("status") != "available":
            return _err("Загрузка этой модели недоступна.", "unavailable")
        with self._lock:
            if self._download_cancel is not None:
                return _err("Загрузка уже выполняется.", "busy")
            self._download_cancel = DownloadCancellation()
        cancel = self._download_cancel

        def progress(value: dict[str, Any]) -> None:
            self._push(
                "download_progress",
                {
                    "modelId": model_id,
                    "bytes": value.get("bytes", 0),
                    "total": value.get("total", 0),
                    "phase": value.get("phase"),
                    "ratio": value.get("ratio"),
                    "speedBps": value.get("speed_bps"),
                    "complete": bool(value.get("complete")),
                },
            )

        def work() -> None:
            try:
                download_model(model, self.models_dir, cancel=cancel, on_progress=progress)
                self._push("download_done", {"modelId": model_id, "ok": True, "error": None})
            except Exception as exc:
                self._push("download_done", {"modelId": model_id, "ok": False, "error": str(exc)})
            finally:
                with self._lock:
                    self._download_cancel = None

        self._spawn(work, "download")
        return _ok({"started": True})

    def cancelDownload(self) -> dict[str, Any]:
        with self._lock:
            cancel = self._download_cancel
        if cancel is None:
            return _ok({"active": False})
        accepted = cancel.set()
        return _ok({"active": True, "accepted": accepted})

    def listMarket(self) -> dict[str, Any]:
        cache = load_market_cache(self.models_dir)
        known = {
            str(model.get("sha256") or "").lower()
            for model in all_models(self.models_dir)
            if model.get("sha256")
        }
        return _ok(
            {
                "fetchedAt": cache["fetched_at"],
                "stale": cache["stale"],
                "errors": cache["errors"],
                "offers": [_public_offer(offer, offer.get("sha256", "").lower() in known) for offer in cache["offers"]],
            }
        )

    def resolveMarketLink(self, url: str) -> dict[str, Any]:
        """Выбрать файл закреплённого рынка по ссылке. Веса не скачиваются."""
        if not isinstance(url, str) or not url.strip():
            return _err("Вставьте ссылку.", "invalid")
        cache = load_market_cache(self.models_dir)
        try:
            matched = match_market_link(url, cache["offers"])
        except MarketError as exc:
            return _err(str(exc), "invalid")
        known = {
            str(model.get("sha256") or "").lower()
            for model in all_models(self.models_dir)
            if model.get("sha256")
        }
        wanted = set(matched["offer_ids"])
        offers = [
            _public_offer(offer, str(offer.get("sha256") or "").lower() in known)
            for offer in cache["offers"]
            if offer.get("id") in wanted
        ]
        return _ok(
            {
                "offerId": matched["offer_id"],
                "repo": matched["repo"],
                "filename": matched["filename"],
                "offers": offers,
            }
        )

    def refreshMarket(self) -> dict[str, Any]:
        with self._lock:
            if self._market_busy:
                return _err("Список рынка уже обновляется.", "busy")
            self._market_busy = True

        def work() -> None:
            try:
                payload = refresh_market(self.models_dir)
                self._push(
                    "market_refreshed",
                    {
                        "ok": not payload["errors"] or bool(payload["offers"]),
                        "stale": payload["stale"],
                        "error": payload["errors"][0]["error"] if payload["errors"] and not payload["offers"] else None,
                    },
                )
            except Exception as exc:
                self._push("market_refreshed", {"ok": False, "stale": False, "error": str(exc)})
            finally:
                with self._lock:
                    self._market_busy = False

        self._spawn(work, "market refresh")
        return _ok({"started": True})

    def downloadMarketModel(self, offer_id: str) -> dict[str, Any]:
        if not isinstance(offer_id, str):
            return _err("Некорректный файл рынка.", "invalid")
        offer = find_cached_offer(self.models_dir, offer_id)
        if offer is None:
            return _err("Файл не найден в списке рынка. Обновите список.", "not_found")
        with self._lock:
            if self._download_cancel is not None:
                return _err("Загрузка уже выполняется.", "busy")
            self._download_cancel = DownloadCancellation()
        cancel = self._download_cancel

        def progress(value: dict[str, Any]) -> None:
            self._push(
                "download_progress",
                {
                    "modelId": offer_id,
                    "bytes": value.get("bytes", 0),
                    "total": value.get("total", 0),
                    "phase": value.get("phase"),
                    "ratio": value.get("ratio"),
                    "speedBps": value.get("speed_bps"),
                    "complete": bool(value.get("complete")),
                },
            )

        def work() -> None:
            try:
                live = confirm_offer(offer)
                record = register_offer(live, self.models_dir)
                download_model(record, self.models_dir, cancel=cancel, on_progress=progress)
                self._push("download_done", {"modelId": offer_id, "ok": True, "error": None})
            except Exception as exc:
                self._push("download_done", {"modelId": offer_id, "ok": False, "error": str(exc)})
            finally:
                with self._lock:
                    self._download_cancel = None

        self._spawn(work, "market download")
        return _ok({"started": True})

    def importCustomModel(self, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            return _err("Некорректные данные модели.")
        source = Path(str(data.get("sourcePath") or ""))
        if not source.is_file():
            return _err("Файл модели не найден.", "not_found")
        name = str(data.get("name") or "").strip()
        if not name:
            return _err("Укажите название модели.", "empty_name")
        codes = [str(item).strip() for item in (data.get("languageCodes") or []) if str(item).strip()]
        if not codes:
            return _err("Укажите языковые коды модели.", "no_languages")
        try:
            context_size = int(data.get("contextSize") or 4096)
        except (TypeError, ValueError):
            return _err("Некорректный размер контекста.", "bad_context")

        def work() -> None:
            try:
                model = import_custom_model(
                    source,
                    name,
                    codes,
                    root=self.models_dir,
                    context_size=context_size,
                    license_name=str(data.get("licenseName") or "Не указана"),
                )
                self._push("custom_model_imported", {"ok": True, "error": None, "model": {"id": model["id"]}})
            except Exception as exc:
                self._push("custom_model_imported", {"ok": False, "error": str(exc), "model": None})

        self._spawn(work, "custom_model_import")
        return _ok({"started": True})

    def selectModelForProject(self, model_id: str) -> dict[str, Any]:
        store = self._active_store()
        if store is None:
            return _err("Сначала выберите проект.", "no_project")
        try:
            model = get_model(model_id, root=self.models_dir)
        except KeyError:
            return _err("Модель не найдена.", "not_found")
        if not installed(model, self.models_dir):
            return _err("Сначала установите или загрузите модель.", "not_installed")
        targets = store.target_languages()
        source = store.project.get("source_lang") or AUTO_LANGUAGE
        unsupported = [t for t in targets if not supports_language(model, t)]
        if source != AUTO_LANGUAGE and not supports_language(model, source):
            unsupported.insert(0, source)
        if unsupported:
            labels = ", ".join(language_label(code) for code in dict.fromkeys(unsupported))
            return _err(
                f"Модель не перечисляет поддержку: {labels}. Измените языки проекта в настройках.",
                "language_unsupported",
            )
        store.update_settings(model_id=model_id)
        return _ok(_serialize_project(store))

    # ------------------------------------------------------------------ glossary

    def listGlossary(self, target_lang: str) -> dict[str, Any]:
        store = self._active_store()
        if store is None:
            return _err("Сначала выберите проект.", "no_project")
        return _ok(store.glossary(target_lang=str(target_lang)))

    def addGlossaryTerm(self, data: dict[str, Any]) -> dict[str, Any]:
        store = self._active_store()
        if store is None:
            return _err("Сначала выберите проект.", "no_project")
        if not isinstance(data, dict):
            return _err("Некорректные данные термина.")
        try:
            store.add_glossary_term(
                str(data.get("source") or ""),
                str(data.get("target") or ""),
                target_lang=str(data.get("targetLang") or "") or None,
            )
        except ValueError as exc:
            return _err(str(exc))
        return _ok(None)

    def updateGlossaryTerm(self, data: dict[str, Any]) -> dict[str, Any]:
        store = self._active_store()
        if store is None:
            return _err("Сначала выберите проект.", "no_project")
        try:
            store.update_glossary_term(
                int(data.get("id")),
                str(data.get("source") or ""),
                str(data.get("target") or ""),
            )
        except (KeyError, ValueError, TypeError) as exc:
            return _err(f"Не удалось обновить термин: {exc}")
        return _ok(None)

    def deleteGlossaryTerm(self, term_id: int) -> dict[str, Any]:
        store = self._active_store()
        if store is None:
            return _err("Сначала выберите проект.", "no_project")
        try:
            store.delete_glossary_term(int(term_id))
        except (KeyError, TypeError, ValueError) as exc:
            return _err(f"Не удалось удалить термин: {exc}")
        return _ok(None)

    # ------------------------------------------------------------------ dialogs and system

    def resolveModelPath(self) -> dict[str, Any]:
        if self._window is None:
            return _ok(None)
        try:
            paths = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("GGUF (*.gguf)", "Все файлы (*.*)"),
            )
        except Exception:
            return _ok(None)
        if not paths:
            return _ok(None)
        chosen = paths[0] if isinstance(paths, (list, tuple)) else paths
        return _ok(str(chosen))

    def resolveImportPaths(self) -> dict[str, Any]:
        if self._window is None:
            return _err("Окно приложения ещё не готово.", "no_window")
        try:
            paths = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=True,
                file_types=IMPORT_EXTENSIONS,
            )
        except Exception as exc:
            return _err(f"Не удалось открыть проводник: {exc}")
        if not paths:
            return _ok([])
        return _ok([str(item) for item in paths])

    def resolveGGUFPath(self) -> dict[str, Any]:
        """Нативный диалог выбора локального GGUF-файла."""
        if self._window is None:
            return _err("Окно приложения ещё не готово.", "no_window")
        try:
            paths = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=GGUF_FILE_TYPES,
            )
        except Exception as exc:
            return _err(f"Не удалось открыть проводник: {exc}")
        if not paths:
            return _ok(None)
        return _ok(str(paths[0]) if isinstance(paths, (list, tuple)) else str(paths))

    def claimDroppedFile(self, name: str) -> dict[str, Any]:
        """Путь файла, который только что перетащили в окно. Совпадение по имени."""
        wanted = str(name or "")
        paths = _dnd_state.get("paths") or []
        for item in list(paths):
            base, full = item
            if str(base) == wanted or Path(str(full)).name == wanted:
                paths.remove(item)
                return _ok(str(full))
        return _ok(None)

    def resolveExportPath(self, default_name: str, allowed_extensions: list[str]) -> dict[str, Any]:
        if self._window is None:
            return _ok(None)
        extensions = [
            ext if ext.startswith(".") else f".{ext}" for ext in (allowed_extensions or [".txt"])
        ]
        patterns = [f"*{ext}" for ext in extensions]
        try:
            paths = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                allow_multiple=False,
                file_types=(f"Поддерживаемые форматы ({'; '.join(patterns)})",),
                save_filename=str(default_name or "export"),
            )
        except Exception:
            return _ok(None)
        if not paths:
            return _ok(None)
        chosen = str(paths[0]) if isinstance(paths, (list, tuple)) else str(paths)
        suffix = Path(chosen).suffix.lower()
        if suffix not in extensions:
            chosen = f"{chosen}{extensions[0]}"
        return _ok(chosen)

    def getExportExtensions(self, doc_id: str) -> dict[str, Any]:
        """Допустимые расширения экспорта по формату документа (матрица форматов)."""
        store = self._active_store()
        if store is None:
            return _err("Сначала выберите проект.", "no_project")
        try:
            record = store.document(doc_id)
            parsed = store.parsed(doc_id)
        except KeyError:
            return _err("Документ не найден.", "not_found")
        allowed = list(EXPORT_ALLOWED.get(record.format, (".txt", ".md")))
        if parsed.metadata.get("pdfLayout") == "book" and ".pdf" not in allowed:
            allowed.insert(0, ".pdf")
        return _ok(allowed)

    def revealPath(self, path: str) -> dict[str, Any]:
        if not path:
            return _err("Путь не указан.", "no_path")
        target = Path(path)
        try:
            if sys.platform == "win32":
                os.startfile(str(target))  # noqa: S606
            elif target.is_dir():
                webbrowser.open(target.as_uri())
            else:
                webbrowser.open(target.parent.as_uri())
        except OSError as exc:
            return _err(f"Не удалось открыть: {exc}")
        return _ok(None)

    def getDataDirs(self) -> dict[str, Any]:
        return _ok(
            {
                "projectsDir": str(self.projects_dir),
                "modelsDir": str(self.models_dir),
                "dataDir": str(self.data_dir),
            }
        )

    # ------------------------------------------------------------------ scratch dialog

    def askScratch(self, data: dict[str, Any]) -> dict[str, Any]:
        """Один ход диалога без проекта. Ответ приходит событиями chat_token и chat_done."""
        if not isinstance(data, dict):
            return _err("Некорректные данные диалога.")
        text = str(data.get("text") or "").strip()
        attachment = str(data.get("attachment") or "").strip()[:8000]
        if not text and not attachment:
            return _err("Введите текст.", "empty")
        if len(text) > SCRATCH_LIMIT:
            return _err(
                f"Фрагмент длиннее {SCRATCH_LIMIT} знаков. "
                "Бросьте файл в перевод: он вернётся отдельным документом.",
                "too_long",
            )
        model_id = str(data.get("modelId") or "").strip()
        source = str(data.get("sourceLang") or "auto").strip() or "auto"
        target = str(data.get("targetLang") or "").strip()
        mode = str(data.get("mode") or "translate").strip() or "translate"
        if mode not in ("translate", "ask"):
            return _err("Неизвестный режим диалога.", "mode")
        if mode == "translate" and not target:
            return _err("Выберите язык перевода.", "no_target")
        if mode == "translate" and source != "auto" and source == target:
            return _err("Язык оригинала и перевода должны различаться.", "same_language")
        try:
            model = load_model_for_scratch(model_id, self.models_dir)
        except ScratchRejected as exc:
            return _err(str(exc), exc.code)
        if not installed(model, self.models_dir):
            return _err("Сначала скачайте модель.", "model_missing")
        if not _runtime_available():
            return _err(
                "В этой сборке не найден llama-cpp-python. "
                "Вес модели уже можно хранить, перевод без runtime не запустится.",
                "runtime_missing",
            )
        if mode == "translate":
            if source != "auto" and not supports_language(model, source):
                return _err("Модель не перечисляет исходный язык.", "language_unsupported")
            if not supports_language(model, target):
                return _err("Модель не перечисляет язык перевода.", "language_unsupported")
        previous = _scratch_pairs(data.get("previous"))
        context = str(data.get("context") or "").strip()[:800]
        try:
            request_id = self._scratch.submit(
                model, text, source, target, previous, context, mode, attachment
            )
        except ScratchRejected as exc:
            return _err(str(exc), exc.code)
        return _ok({"requestId": request_id})

    def cancelScratch(self) -> dict[str, Any]:
        self._scratch.cancel()
        return _ok({"accepted": True})

    def listDialogs(self) -> dict[str, Any]:
        return _ok(list_dialogs(self.data_dir))

    def loadDialog(self, dialog_id: str) -> dict[str, Any]:
        try:
            dialog = load_dialog(self.data_dir, dialog_id)
        except ValueError as exc:
            return _err(str(exc), "bad_id")
        if dialog is None:
            return _err("Диалог не найден.", "not_found")
        return _ok(dialog)

    def saveDialog(self, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            return _err("Некорректные данные диалога.")
        try:
            return _ok(save_dialog(self.data_dir, data))
        except ValueError as exc:
            return _err(str(exc), "bad_id")

    def deleteDialog(self, dialog_id: str) -> dict[str, Any]:
        try:
            delete_dialog(self.data_dir, dialog_id)
        except ValueError as exc:
            return _err(str(exc), "bad_id")
        return _ok(None)

    def readChatAttachment(self, path: str) -> dict[str, Any]:
        try:
            return _ok(read_attachment(Path(path)))
        except ValueError as exc:
            return _err(str(exc), "attachment")

    def dialogMeter(self, data: dict[str, Any]) -> dict[str, Any]:
        """Оценка контекста и снимок нагрузки. Это не замер качества и не токены модели."""
        if not isinstance(data, dict):
            data = {}
        model = None
        model_id = str(data.get("modelId") or "").strip()
        if model_id:
            try:
                model = get_model(model_id, root=self.models_dir)
            except KeyError:
                model = None
        limit = int((model or {}).get("default_context") or 2048)
        if limit <= 0:
            limit = 2048
        try:
            chars = max(0, int(data.get("charCount") or 0))
        except (TypeError, ValueError):
            chars = 0
        used = chars // 4
        percent = min(100, round(used * 100 / limit))
        cpu = None
        ram = None
        try:
            import psutil

            cpu = round(float(psutil.cpu_percent(interval=None)), 1)
            ram = round(float(psutil.virtual_memory().percent), 1)
        except (OSError, AttributeError, ValueError):
            cpu = None
            ram = None
        layers = 0
        if model is not None and self.hardware is not None:
            try:
                layers = gpu_layers_for(self.hardware, model)
            except (TypeError, ValueError):
                layers = 0
        if layers < 0:
            placement = "GPU"
        elif layers > 0:
            placement = f"GPU, {layers} слоёв"
        else:
            placement = "CPU"
        return _ok(
            {
                "cpuPercent": cpu,
                "ramPercent": ram,
                "placement": placement,
                "contextLimit": limit,
                "contextUsed": used,
                "contextPercent": percent,
            }
        )

    # ------------------------------------------------------------------ shutdown

    def finishClose(self) -> dict[str, Any]:
        """Книга закрылась: разрешить окну уйти."""
        self._close_permitted = True
        closer = self._window_closer
        if closer is not None:
            closer()
        return _ok(None)

    def closeGracefully(self) -> dict[str, Any]:
        self._closing = True
        with self._lock:
            cancel = self._download_cancel
        if cancel is not None:
            cancel.set()
        with self._lock:
            workers = list(self._workers)
        for worker in workers:
            if worker.is_alive():
                worker.join(timeout=3)
        self._scratch.close()
        for queue in self.project_queues.values():
            queue.close(timeout=1)
        return _ok(None)

    def resumeQueuedTasks(self) -> dict[str, Any]:
        """Re-enqueue persisted queued tasks for every project after app start."""
        for store in self.projects:
            has_queued = any(task["status"] == "queued" for task in store.tasks())
            if has_queued:
                self._queue_for(store).start()
        return _ok(None)
