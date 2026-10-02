from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import dotlingo.api as api_module
from dotlingo.api import Api
from dotlingo.languages import supports_language
from dotlingo.model_download import DownloadCancelled
from dotlingo.models import catalog
from dotlingo.storage import ProjectStore


def _available_model() -> dict:
    for model in catalog():
        if model.get("status") == "available" and supports_language(model, "ru"):
            return model
    pytest.skip("В каталоге нет доступной модели с поддержкой ru.")


def _make_api(tmp_path: Path, *, sync: bool = False) -> Api:
    api = Api(tmp_path)
    if sync:
        api._spawn = lambda work, name: work()  # noqa: SLF001 - синхронный запуск воркеров в тестах
    return api


def _create_project(api: Api, model_id: str = "", targets: list[str] | None = None) -> dict:
    return api.createProject(
        {
            "title": "Тестовый проект",
            "modelId": model_id,
            "sourceLang": "auto",
            "targetLangs": ["ru"] if targets is None else targets,
        }
    )


def _import_txt(api: Api, tmp_path: Path, name: str = "sample.txt", text: str = "") -> None:
    source = tmp_path / name
    source.write_text(text or "Первый абзац текста.\n\nВторой абзац текста.", encoding="utf-8")
    result = api.importDocuments([str(source)])
    assert result["ok"] is True


class FakeQueue:
    instances: list["FakeQueue"] = []

    def __init__(self, store, model_root, on_event=None) -> None:
        self.store = store
        self.calls: list[tuple[str, str]] = []
        FakeQueue.instances.append(self)

    def start(self) -> None:
        self.calls.append(("start", ""))

    def enqueue(self, task_id: str) -> None:
        self.calls.append(("enqueue", task_id))

    def resume(self, task_id: str) -> None:
        self.calls.append(("resume", task_id))

    def pause(self, task_id: str) -> None:
        self.calls.append(("pause", task_id))

    def cancel(self, task_id: str) -> None:
        self.calls.append(("cancel", task_id))

    def close(self, timeout: float = 1) -> None:
        self.calls.append(("close", ""))


@pytest.fixture()
def fake_queue(monkeypatch: pytest.MonkeyPatch):
    FakeQueue.instances = []
    monkeypatch.setattr(api_module, "TaskQueue", FakeQueue)
    return FakeQueue


# --------------------------------------------------------------------- preferences


def test_list_languages(tmp_path: Path) -> None:
    data = Api(tmp_path).listLanguages()["data"]
    assert data["auto"]["code"] == "auto"
    codes = {item["code"] for item in data["languages"]}
    assert {"ru", "en"} <= codes


def test_resolve_model_path_without_window(tmp_path: Path) -> None:
    assert Api(tmp_path).resolveModelPath()["data"] is None


def test_preferences_roundtrip(tmp_path: Path) -> None:
    api = _make_api(tmp_path)
    assert api.getPreferences()["ok"] is True
    result = api.setPreferences({"theme": "light"})
    assert result["ok"] is True
    assert result["data"]["theme"] == "light"


# --------------------------------------------------------------------- projects


def test_project_crud(tmp_path: Path) -> None:
    model = _available_model()
    api = _make_api(tmp_path)
    created = _create_project(api, model["id"], targets=["ru", "en"])
    assert created["ok"] is True
    project_id = created["data"]["id"]
    assert api.getActiveProject()["data"]["id"] == project_id

    listed = api.listProjects()["data"]
    assert [item["id"] for item in listed] == [project_id]
    assert listed[0]["documentCount"] == 0

    renamed = api.renameProject(project_id, "Новое имя")
    assert renamed["ok"] is True
    assert renamed["data"]["title"] == "Новое имя"

    deleted = api.deleteProject(project_id)
    assert deleted["ok"] is True
    assert api.listProjects()["data"] == []
    assert api.getActiveProject()["data"] is None
    assert list((tmp_path / "projects").iterdir()) == []


def test_project_validation(tmp_path: Path) -> None:
    model = _available_model()
    api = _make_api(tmp_path)
    assert _create_project(api, model["id"], targets=[])["code"] == "no_targets"
    from dotlingo.languages import LANGUAGES

    unsupported = next((code for code in LANGUAGES if not supports_language(model, code)), None)
    if unsupported is None:
        pytest.skip("Модель поддерживает все известные языки.")
    result = _create_project(api, model["id"], targets=[unsupported])
    assert result["code"] == "language_unsupported"
    assert api.renameProject("missing", "x")["ok"] is False
    assert api.deleteProject("missing")["ok"] is False


def test_update_project_settings(tmp_path: Path) -> None:
    model = _available_model()
    api = _make_api(tmp_path)
    _create_project(api, model["id"], targets=["ru"])
    result = api.updateProjectSettings({"targetLangs": []})
    assert result["code"] == "no_targets"
    result = api.updateProjectSettings({"targetLangs": ["ru"], "sourceLang": "ru"})
    assert result["code"] == "same_language"
    result = api.updateProjectSettings(
        {"targetLangs": ["ru", "en"], "sourceLang": "auto", "context": "контекст", "rules": "правила"}
    )
    assert result["ok"] is True
    assert sorted(result["data"]["targetLangs"]) == ["en", "ru"]


# --------------------------------------------------------------------- documents


def test_document_import_edit_export(tmp_path: Path) -> None:
    model = _available_model()
    api = _make_api(tmp_path, sync=True)
    _create_project(api, model["id"], targets=["ru"])
    _import_txt(api, tmp_path)

    documents = api.listDocuments()["data"]
    assert len(documents) == 1
    doc_id = documents[0]["id"]
    assert documents[0]["progressByTarget"]["ru"]["total"] > 0
    spectrum = documents[0]["spectrumByTarget"]["ru"]
    assert spectrum, "спектр документа не должен быть пуст"
    assert any(value == 1.0 for value in spectrum) is False  # ещё нет переводов

    detail = api.getDocument(doc_id)["data"]
    assert detail["blocks"], "блоки должны быть"
    order = next(block["order"] for block in detail["blocks"] if block["translatable"])

    saved = api.saveEdit(doc_id, order, "Исправленный перевод.", "ru")
    assert saved["ok"] is True
    edited = api.getDocument(doc_id)["data"]
    assert edited["translations"]["ru"][str(order)] == "Исправленный перевод."
    assert edited["editedFlags"]["ru"][str(order)] is True
    assert edited["machineDrafts"]["ru"] == {}
    spectrum = api.listDocuments()["data"][0]["spectrumByTarget"]["ru"]
    assert any(value == 1.0 for value in spectrum) is True

    destination = tmp_path / "export" / "sample.translated-ru.txt"
    destination.parent.mkdir(exist_ok=True)
    exported = api.exportDocument(doc_id, "ru", str(destination))
    assert exported["ok"] is True
    assert destination.read_text(encoding="utf-8").strip() != ""

    assert api.getDocument("missing")["ok"] is False


# --------------------------------------------------------------------- translation


def test_enqueue_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model = _available_model()
    api = _make_api(tmp_path, sync=True)
    _create_project(api)  # без модели
    _import_txt(api, tmp_path)
    doc_id = api.listDocuments()["data"][0]["id"]

    assert api.enqueueTranslation({"documentIds": [doc_id], "targetLangs": ["ru"]})["code"] == "model_missing"

    _create_project_ok = api.updateProjectSettings({"targetLangs": ["ru"], "modelId": model["id"]})
    assert _create_project_ok["ok"] is True
    monkeypatch.setattr(api_module, "installed", lambda m, root=None: True)
    monkeypatch.setattr(api_module, "_runtime_available", lambda: False)
    assert api.enqueueTranslation({"documentIds": [doc_id], "targetLangs": ["ru"]})["code"] == "runtime_missing"

    monkeypatch.setattr(api_module, "_runtime_available", lambda: True)
    monkeypatch.setattr(api_module, "supports_language", lambda m, code: False)
    assert api.enqueueTranslation({"documentIds": [doc_id], "targetLangs": ["ru"]})["code"] == "language_unsupported"


def test_enqueue_success_and_actions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_queue: type[FakeQueue]) -> None:
    model = _available_model()
    api = _make_api(tmp_path, sync=True)
    _create_project(api, model["id"], targets=["ru", "en"])
    _import_txt(api, tmp_path)
    doc_id = api.listDocuments()["data"][0]["id"]

    monkeypatch.setattr(api_module, "installed", lambda m, root=None: True)
    monkeypatch.setattr(api_module, "_runtime_available", lambda: True)

    result = api.enqueueTranslation({"documentIds": [doc_id], "targetLangs": ["ru", "en"]})
    assert result["ok"] is True
    assert len(result["data"]["taskIds"]) == 2
    assert [call[0] for call in fake_queue.instances[-1].calls] == ["enqueue", "enqueue"]

    tasks = api.listTasks()["data"]
    assert len(tasks) == 2
    assert {task["status"] for task in tasks} == {"queued"}

    task_id = result["data"]["taskIds"][0]
    assert api.pauseTask(task_id)["ok"] is True
    assert api.cancelTask(task_id)["ok"] is True
    assert api.resumeTask("missing")["ok"] is False

    store = ProjectStore(api.projects_dir / api.active_project_id) if api.active_project_id else None
    assert store is not None
    store.set_task_status(task_id, "complete")
    assert api.resumeTask(task_id)["code"] == "already_complete"


# --------------------------------------------------------------------- glossary


def test_glossary_crud(tmp_path: Path) -> None:
    api = _make_api(tmp_path)
    _create_project(api)
    added = api.addGlossaryTerm({"source": "term", "target": "термин", "targetLang": "ru"})
    assert added["ok"] is True

    terms = api.listGlossary("ru")["data"]
    assert terms == [{"id": 1, "source": "term", "target": "термин"}]
    term_id = terms[0]["id"]

    updated = api.updateGlossaryTerm({"id": term_id, "source": "term", "target": "термин 2"})
    assert updated["ok"] is True
    assert api.listGlossary("ru")["data"][0]["target"] == "термин 2"

    assert api.updateGlossaryTerm({"id": term_id, "source": "", "target": "x"})["ok"] is False
    assert api.deleteGlossaryTerm(999)["ok"] is False
    assert api.deleteGlossaryTerm(term_id)["ok"] is True
    assert api.listGlossary("ru")["data"] == []


# --------------------------------------------------------------------- models


def test_list_models_catalog(tmp_path: Path) -> None:
    api = _make_api(tmp_path)
    result = api.listModels()
    assert result["ok"] is True
    models = result["data"]["models"]
    assert models, "каталог не должен быть пуст"
    assert all(item["installState"] in {"installed", "available", "missing", "unverified"} for item in models)
    assert {item["id"] for item in models} >= {model["id"] for model in catalog()}


# --------------------------------------------------------------------- hardware


def test_hardware_cached_until_rerun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dotlingo.hardware import HardwareSnapshot

    api = _make_api(tmp_path, sync=True)
    assert api.getHardware()["data"] is None

    snapshot = HardwareSnapshot(
        cpu_threads=8,
        ram_total_gb=16.0,
        ram_available_gb=8.0,
        disk_free_gb=100.0,
        gpu_names=("NVIDIA Demo",),
        gpu_vram_gb=(4.0,),
        llama_runtime_available=True,
        llama_gpu_offload_available=None,
    )
    monkeypatch.setattr(api_module, "detect", lambda _path: snapshot)
    assert api.detectHardware()["ok"] is True
    assert (tmp_path / "hardware.json").is_file()

    # Новый экземпляр поднимает кэш мгновенно, без повторной проверки.
    cached = Api(tmp_path).getHardware()["data"]
    assert cached is not None
    assert cached["cpuThreads"] == 8
    assert cached["gpuNames"] == ["NVIDIA Demo"]
    assert cached["detectedAt"]


# --------------------------------------------------------------------- close semantics


def test_download_cancel_and_close(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model = _available_model()

    def fake_download(model_dict, root, *, cancel, on_progress=None):
        deadline = time.monotonic() + 10
        while not cancel.is_set() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not cancel.is_set():
            raise AssertionError("отмена загрузки не наблюдалась")
        raise DownloadCancelled("Загрузка отменена.")

    monkeypatch.setattr(api_module, "download_model", fake_download)
    api = _make_api(tmp_path)

    started = api.downloadModel(model["id"])
    assert started["ok"] is True
    assert api.downloadModel(model["id"])["code"] == "busy"

    cancelled = api.cancelDownload()
    assert cancelled["ok"] is True

    api.closeGracefully()
    assert api._download_cancel is None  # noqa: SLF001 - проверка внутреннего состояния после close


# --------------------------------------------------------------------- smoke entry


def test_smoke_entrypoint(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": str(repo_root / "src")}
    completed = subprocess.run(
        [sys.executable, "-m", "dotlingo", "--smoke-test", "--data-dir", str(tmp_path / "smoke")],
        capture_output=True,
        text=True,
        env=env,
        cwd=repo_root,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert '"ok": true' in completed.stdout
