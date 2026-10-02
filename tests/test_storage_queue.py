from __future__ import annotations

import hashlib
from pathlib import Path

import dotlingo.task_queue as task_queue_module
from dotlingo.formats import export_document
from dotlingo.storage import ProjectStore
from dotlingo.task_queue import TaskQueue, build_chunks


def test_project_source_is_immutable_and_running_task_recovers(tmp_path: Path) -> None:
    source = tmp_path / "book.txt"
    source.write_text("One paragraph.\n\nSecond paragraph.", encoding="utf-8")
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    store = ProjectStore.create(tmp_path / "projects", "Test")
    record = store.import_file(source)
    saved_source = record.source_path.read_bytes()
    task_id = store.create_task(record.id, "test-model", build_chunks(store.blocks(record.id)))
    store.set_task_status(task_id, "running", "worker started")
    first = store.pending_segments(task_id)[0]
    store.save_segment(task_id, first["block_ord"], first["segment_ord"], "Один абзац.")

    reopened = ProjectStore(store.root)
    assert reopened.task(task_id)["status"] == "interrupted"
    assert len(reopened.pending_segments(task_id)) == 1
    assert reopened.completed_segments(task_id, 2)[0]["translation"] == "Один абзац."
    reopened.resume_task(task_id)
    second = reopened.pending_segments(task_id)[0]
    reopened.save_segment(task_id, second["block_ord"], second["segment_ord"], "Второй абзац.")
    reopened.finish_task(task_id)

    translations = reopened.translations(record.id)
    assert translations == {0: "Один абзац.", 2: "Второй абзац."}
    output = tmp_path / "translated.txt"
    export_document(record.source_path, output, reopened.parsed(record.id), translations)
    assert output.read_bytes() == "Один абзац.\r\n\r\nВторой абзац.".encode("utf-8")
    assert record.source_path.read_bytes() == saved_source
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    assert reopened.task(task_id)["status"] == "complete"


def test_confirmed_pairs_keep_human_edits_apart_from_the_machine_draft(tmp_path: Path) -> None:
    source = tmp_path / "doc.txt"
    source.write_text("Harbour master.\n\nRiver.", encoding="utf-8")
    store = ProjectStore.create(tmp_path / "projects", "Test")
    document = store.import_file(source)
    task_id = store.create_task(document.id, "hy-mt2-7b-q4km", build_chunks(store.blocks(document.id)))
    pending = store.pending_segments(task_id)
    for segment in pending:
        store.save_segment(task_id, segment["block_ord"], segment["segment_ord"], "черновик")
    store.finish_task(task_id)
    assert store.machine_draft(document.id, "ru")
    assert store.confirmed_pairs("ru") == []
    first = pending[0]["block_ord"]
    store.save_edit(document.id, first, "Начальник гавани.", target_lang="ru")
    pairs = store.confirmed_pairs("ru")
    assert pairs == [("Harbour master.", "Начальник гавани.")]
    assert store.translation_flags(document.id, "ru")[first] is True


def test_cancel_queued_task_persists_state(tmp_path: Path) -> None:
    source = tmp_path / "doc.txt"
    source.write_text("Text", encoding="utf-8")
    store = ProjectStore.create(tmp_path / "projects", "Test")
    document = store.import_file(source)
    task_id = store.create_task(document.id, "test-model", build_chunks(store.blocks(document.id)))
    queue = TaskQueue(store, tmp_path / "models")
    queue.cancel(task_id)
    assert store.task(task_id)["status"] == "cancelled"


def test_translation_stops_if_project_source_copy_was_changed(tmp_path: Path) -> None:
    source = tmp_path / "doc.txt"
    source.write_text("Original text.", encoding="utf-8")
    store = ProjectStore.create(tmp_path / "projects", "Test")
    document = store.import_file(source)
    task_id = store.create_task(document.id, "qwen3-1.7b-q8", build_chunks(store.blocks(document.id)))
    document.source_path.write_text("Changed copy.", encoding="utf-8")

    TaskQueue(store, tmp_path / "models")._execute(task_id)

    task = store.task(task_id)
    assert task["status"] == "failed"
    assert "Копия оригинала изменилась" in task["error"]


def test_backend_error_is_persisted_for_recovery(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "doc.txt"
    source.write_text("Original text.", encoding="utf-8")
    store = ProjectStore.create(tmp_path / "projects", "Test")
    document = store.import_file(source)
    task_id = store.create_task(document.id, "qwen3-1.7b-q8", build_chunks(store.blocks(document.id)))
    monkeypatch.setattr(task_queue_module.importlib.util, "find_spec", lambda _: None)

    TaskQueue(store, tmp_path / "models")._execute(task_id)

    task = store.task(task_id)
    assert task["status"] == "failed"
    assert "runtime" in task["error"]


def test_retranslation_replaces_only_translation_not_source(tmp_path: Path) -> None:
    source = tmp_path / "doc.txt"
    source.write_text("Original.", encoding="utf-8")
    store = ProjectStore.create(tmp_path / "projects", "Test")
    document = store.import_file(source)
    chunks = build_chunks(store.blocks(document.id))
    task1 = store.create_task(document.id, "model-a", chunks)
    segment = store.pending_segments(task1)[0]
    store.save_segment(task1, segment["block_ord"], segment["segment_ord"], "Первый.")
    store.finish_task(task1)
    task2 = store.create_task(document.id, "model-b", chunks)
    segment = store.pending_segments(task2)[0]
    store.save_segment(task2, segment["block_ord"], segment["segment_ord"], "Другой.")
    store.finish_task(task2)
    assert store.translations(document.id) == {0: "Другой."}
    assert document.source_path.read_text(encoding="utf-8") == "Original."
