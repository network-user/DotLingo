from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotlingo.formats import (
    Block,
    ParsedDocument,
    import_document,
    load_parsed,
    save_parsed,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SourceIntegrityError(RuntimeError):
    """The immutable project copy is missing or no longer matches its import hash."""


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    return connection


SCHEMA = """
CREATE TABLE IF NOT EXISTS project (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  source_lang TEXT NOT NULL,
  target_lang TEXT NOT NULL,
  target_langs_json TEXT NOT NULL DEFAULT '[]',
  model_id TEXT NOT NULL DEFAULT '',
  context TEXT NOT NULL DEFAULT '',
  rules TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  source_rel TEXT NOT NULL,
  format TEXT NOT NULL,
  parsed_path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  warnings_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS document_languages (
  document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
  language_code TEXT NOT NULL,
  model_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blocks (
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  ord INTEGER NOT NULL,
  section_id TEXT NOT NULL,
  section_title TEXT NOT NULL,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  locator_json TEXT NOT NULL,
  prefix TEXT NOT NULL,
  suffix TEXT NOT NULL,
  translatable INTEGER NOT NULL,
  PRIMARY KEY(document_id, ord)
);
CREATE TABLE IF NOT EXISTS translations (
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  target_lang TEXT NOT NULL DEFAULT 'ru',
  block_ord INTEGER NOT NULL,
  text TEXT NOT NULL,
  edited INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(document_id, target_lang, block_ord)
);
CREATE TABLE IF NOT EXISTS glossary (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_lang TEXT NOT NULL,
  target_lang TEXT NOT NULL,
  source TEXT NOT NULL,
  target TEXT NOT NULL,
  UNIQUE(source_lang, target_lang, source, target)
);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  status TEXT NOT NULL,
  model_id TEXT NOT NULL,
  source_lang TEXT NOT NULL DEFAULT '',
  target_lang TEXT NOT NULL DEFAULT '',
  context TEXT NOT NULL DEFAULT '',
  rules TEXT NOT NULL DEFAULT '',
  completed INTEGER NOT NULL DEFAULT 0,
  total INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS segments (
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  block_ord INTEGER NOT NULL,
  segment_ord INTEGER NOT NULL,
  source TEXT NOT NULL,
  translation TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  PRIMARY KEY(task_id, block_ord, segment_ord)
);
CREATE TABLE IF NOT EXISTS task_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  status TEXT NOT NULL,
  message TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  path TEXT NOT NULL,
  format TEXT NOT NULL,
  target_lang TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class DocumentRecord:
    id: str
    name: str
    format: str
    source_path: Path
    sha256: str
    warnings: tuple[str, ...]


class ProjectStore:
    """SQLite-backed project. Source copies are immutable; translations live separately."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.db_path = self.root / "project.sqlite"
        if not self.db_path.is_file():
            raise FileNotFoundError(f"Нет базы проекта: {self.db_path}")
        with _connect(self.db_path) as db:
            db.executescript(SCHEMA)
            self._migrate(db)
            db.execute("UPDATE tasks SET status='interrupted', updated_at=? WHERE status='running'", (_now(),))
            rows = db.execute("SELECT id FROM tasks WHERE status='interrupted'").fetchall()
            for row in rows:
                db.execute(
                    "INSERT INTO task_events(task_id,status,message,created_at) VALUES(?,?,?,?)",
                    (row["id"], "interrupted", "Приложение закрылось до завершения задачи.", _now()),
                )

    @staticmethod
    def _migrate(db: sqlite3.Connection) -> None:
        project_columns = {row["name"] for row in db.execute("PRAGMA table_info(project)")}
        if "target_langs_json" not in project_columns:
            db.execute("ALTER TABLE project ADD COLUMN target_langs_json TEXT NOT NULL DEFAULT '[]'")
        db.execute(
            "UPDATE project SET target_langs_json=json_array(target_lang) "
            "WHERE target_langs_json IS NULL OR target_langs_json='[]' OR target_langs_json=''"
        )

        task_columns = {row["name"] for row in db.execute("PRAGMA table_info(tasks)")}
        added_source = "source_lang" not in task_columns
        added_target = "target_lang" not in task_columns
        added_context = "context" not in task_columns
        added_rules = "rules" not in task_columns
        if added_source:
            db.execute("ALTER TABLE tasks ADD COLUMN source_lang TEXT NOT NULL DEFAULT ''")
        if added_target:
            db.execute("ALTER TABLE tasks ADD COLUMN target_lang TEXT NOT NULL DEFAULT ''")
        if added_context:
            db.execute("ALTER TABLE tasks ADD COLUMN context TEXT NOT NULL DEFAULT ''")
        if added_rules:
            db.execute("ALTER TABLE tasks ADD COLUMN rules TEXT NOT NULL DEFAULT ''")
        if added_source or added_target or added_context or added_rules:
            db.execute(
                "UPDATE tasks SET source_lang=(SELECT source_lang FROM project LIMIT 1), "
                "target_lang=(SELECT target_lang FROM project LIMIT 1), "
                "context=(SELECT context FROM project LIMIT 1), "
                "rules=(SELECT rules FROM project LIMIT 1)"
            )

        translation_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(translations)")
        }
        if "target_lang" not in translation_columns:
            db.execute("ALTER TABLE translations RENAME TO translations_legacy")
            db.execute(
                "CREATE TABLE translations ("
                "document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,"
                "target_lang TEXT NOT NULL DEFAULT 'ru',"
                "block_ord INTEGER NOT NULL,text TEXT NOT NULL,"
                "edited INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL,"
                "PRIMARY KEY(document_id,target_lang,block_ord))"
            )
            db.execute(
                "INSERT INTO translations(document_id,target_lang,block_ord,text,edited,updated_at) "
                "SELECT legacy.document_id,project.target_lang,legacy.block_ord,legacy.text,"
                "legacy.edited,legacy.updated_at FROM translations_legacy legacy CROSS JOIN project"
            )

        export_columns = {row["name"] for row in db.execute("PRAGMA table_info(exports)")}
        if "target_lang" not in export_columns:
            db.execute("ALTER TABLE exports ADD COLUMN target_lang TEXT NOT NULL DEFAULT ''")

    @classmethod
    def create(
        cls,
        base: Path,
        title: str,
        source_lang: str = "en",
        target_lang: str = "ru",
        *,
        target_langs: list[str] | tuple[str, ...] | None = None,
        model_id: str = "",
    ) -> ProjectStore:
        base = Path(base)
        base.mkdir(parents=True, exist_ok=True)
        project_id = str(uuid.uuid4())
        root = base / project_id
        root.mkdir()
        (root / "source").mkdir()
        (root / "output").mkdir()
        db_path = root / "project.sqlite"
        with _connect(db_path) as db:
            db.executescript(SCHEMA)
            timestamp = _now()
            selected_targets = list(
                dict.fromkeys(target_langs if target_langs is not None else [target_lang])
            )
            if not selected_targets:
                raise ValueError("Выберите хотя бы один язык перевода.")
            target_lang = selected_targets[0]
            db.execute(
                "INSERT INTO project(id,title,source_lang,target_lang,target_langs_json,model_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    project_id,
                    title.strip() or "Новый проект",
                    source_lang,
                    target_lang,
                    json.dumps(selected_targets, ensure_ascii=False),
                    model_id,
                    timestamp,
                    timestamp,
                ),
            )
        return cls(root)

    @property
    def project(self) -> dict[str, Any]:
        with _connect(self.db_path) as db:
            row = db.execute("SELECT * FROM project LIMIT 1").fetchone()
        return dict(row)

    def target_languages(self) -> list[str]:
        project = self.project
        try:
            values = json.loads(project.get("target_langs_json", "[]"))
        except (TypeError, json.JSONDecodeError):
            values = []
        if not isinstance(values, list):
            values = []
        targets = list(dict.fromkeys(str(value) for value in values if value))
        return targets or [project["target_lang"]]

    def update_settings(
        self,
        *,
        title: str | None = None,
        source_lang: str | None = None,
        target_lang: str | None = None,
        target_langs: list[str] | tuple[str, ...] | None = None,
        model_id: str | None = None,
        context: str | None = None,
        rules: str | None = None,
    ) -> None:
        values = {
            "title": title,
            "source_lang": source_lang,
            "target_lang": target_lang,
            "target_langs_json": (
                json.dumps(list(dict.fromkeys(target_langs)), ensure_ascii=False)
                if target_langs is not None
                else None
            ),
            "model_id": model_id,
            "context": context,
            "rules": rules,
        }
        if title is not None:
            values["title"] = title.strip() or "Новый проект"
        values = {key: value for key, value in values.items() if value is not None}
        if target_langs is not None:
            targets = list(dict.fromkeys(target_langs))
            if not targets:
                raise ValueError("Выберите хотя бы один язык перевода.")
            values["target_lang"] = target_lang if target_lang in targets else targets[0]
            values["target_langs_json"] = json.dumps(targets, ensure_ascii=False)
        elif target_lang is not None:
            values["target_langs_json"] = json.dumps([target_lang], ensure_ascii=False)
        if not values:
            return
        assignments = ",".join(f"{key}=?" for key in values)
        with _connect(self.db_path) as db:
            db.execute(
                f"UPDATE project SET {assignments}, updated_at=?",
                (*values.values(), _now()),
            )

    def add_glossary_term(
        self, source: str, target: str, *, target_lang: str | None = None
    ) -> None:
        if not source.strip() or not target.strip():
            raise ValueError("Обе формы термина должны быть заполнены.")
        with _connect(self.db_path) as db:
            pair = db.execute("SELECT source_lang,target_lang FROM project LIMIT 1").fetchone()
            db.execute(
                "INSERT OR IGNORE INTO glossary(source_lang,target_lang,source,target) VALUES(?,?,?,?)",
                (
                    pair["source_lang"],
                    target_lang or pair["target_lang"],
                    source.strip(),
                    target.strip(),
                ),
            )

    def glossary(self, target_lang: str | None = None) -> list[dict[str, str]]:
        with _connect(self.db_path) as db:
            pair = db.execute("SELECT source_lang,target_lang FROM project LIMIT 1").fetchone()
            chosen_target = target_lang or pair["target_lang"]
            rows = db.execute(
                "SELECT source,target FROM glossary WHERE source_lang=? AND target_lang=? ORDER BY length(source) DESC, source",
                (pair["source_lang"], chosen_target),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_glossary_term(self, term_id: int, source: str, target: str) -> None:
        if not source.strip() or not target.strip():
            raise ValueError("Обе формы термина должны быть заполнены.")
        with _connect(self.db_path) as db:
            cursor = db.execute(
                "UPDATE glossary SET source=?, target=? WHERE id=?",
                (source.strip(), target.strip(), int(term_id)),
            )
            if cursor.rowcount == 0:
                raise KeyError(term_id)

    def delete_glossary_term(self, term_id: int) -> None:
        with _connect(self.db_path) as db:
            cursor = db.execute("DELETE FROM glossary WHERE id=?", (int(term_id),))
            if cursor.rowcount == 0:
                raise KeyError(term_id)

    def import_file(self, path: Path) -> DocumentRecord:
        path = Path(path)
        parsed = import_document(path)
        doc_id = str(uuid.uuid4())
        source_rel = Path("source") / f"{doc_id}{path.suffix.lower()}"
        destination = self.root / source_rel
        source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        shutil.copyfile(path, destination)
        copied_hash = hashlib.sha256(destination.read_bytes()).hexdigest()
        if copied_hash != source_hash:
            raise OSError("Копия оригинала не прошла проверку SHA-256.")
        parsed_path = self.root / "source" / f"{doc_id}.document.json"
        save_parsed(parsed_path, parsed)
        with _connect(self.db_path) as db:
            db.execute(
                "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)",
                (
                    doc_id,
                    path.name,
                    str(source_rel),
                    parsed.format,
                    str(parsed_path.relative_to(self.root)),
                    source_hash,
                    json.dumps(parsed.warnings, ensure_ascii=False),
                    _now(),
                ),
            )
            db.executemany(
                "INSERT INTO blocks VALUES(?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        doc_id,
                        block.order,
                        block.section,
                        block.section_title,
                        block.kind,
                        block.text,
                        json.dumps(block.locator, ensure_ascii=False),
                        block.prefix,
                        block.suffix,
                        int(block.translatable),
                    )
                    for block in parsed.blocks
                ],
            )
        return DocumentRecord(doc_id, path.name, parsed.format, destination, source_hash, parsed.warnings)

    def documents(self) -> list[DocumentRecord]:
        with _connect(self.db_path) as db:
            rows = db.execute("SELECT * FROM documents ORDER BY created_at").fetchall()
        return [
            DocumentRecord(
                row["id"],
                row["name"],
                row["format"],
                self.root / row["source_rel"],
                row["sha256"],
                tuple(json.loads(row["warnings_json"])),
            )
            for row in rows
        ]

    def document(self, document_id: str) -> DocumentRecord:
        with _connect(self.db_path) as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if row is None:
            raise KeyError(document_id)
        return DocumentRecord(
            row["id"], row["name"], row["format"], self.root / row["source_rel"], row["sha256"],
            tuple(json.loads(row["warnings_json"])),
        )

    def parsed(self, document_id: str) -> ParsedDocument:
        with _connect(self.db_path) as db:
            row = db.execute("SELECT parsed_path FROM documents WHERE id=?", (document_id,)).fetchone()
        if row is None:
            raise KeyError(document_id)
        return load_parsed(self.root / row["parsed_path"])

    def verify_source(self, document_id: str) -> None:
        document = self.document(document_id)
        try:
            digest = _sha256_path(document.source_path)
        except OSError as exc:
            raise SourceIntegrityError("Не удалось проверить сохранённую копию оригинала; задача остановлена.") from exc
        if digest != document.sha256:
            raise SourceIntegrityError("Копия оригинала изменилась после импорта; задача остановлена.")

    def blocks(self, document_id: str) -> list[Block]:
        return list(self.parsed(document_id).blocks)

    def detected_language(self, document_id: str) -> str | None:
        with _connect(self.db_path) as db:
            row = db.execute(
                "SELECT language_code FROM document_languages WHERE document_id=?",
                (document_id,),
            ).fetchone()
        return str(row["language_code"]) if row else None

    def save_detected_language(self, document_id: str, language_code: str, model_id: str) -> None:
        with _connect(self.db_path) as db:
            db.execute(
                "INSERT INTO document_languages(document_id,language_code,model_id,created_at) "
                "VALUES(?,?,?,?) ON CONFLICT(document_id) DO UPDATE SET "
                "language_code=excluded.language_code,model_id=excluded.model_id,created_at=excluded.created_at",
                (document_id, language_code, model_id, _now()),
            )

    def create_task(
        self,
        document_id: str,
        model_id: str,
        chunks: dict[int, list[str]],
        *,
        source_lang: str | None = None,
        target_lang: str | None = None,
    ) -> str:
        task_id = str(uuid.uuid4())
        rows = [(task_id, block, index, source) for block, parts in chunks.items() for index, source in enumerate(parts)]
        if not rows:
            raise ValueError("В документе нет текста для перевода.")
        timestamp = _now()
        with _connect(self.db_path) as db:
            project = db.execute("SELECT * FROM project LIMIT 1").fetchone()
            chosen_source = source_lang or project["source_lang"]
            chosen_target = target_lang or project["target_lang"]
            db.execute(
                "INSERT INTO tasks(id,document_id,status,model_id,source_lang,target_lang,context,rules,total,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    document_id,
                    "queued",
                    model_id,
                    chosen_source,
                    chosen_target,
                    project["context"],
                    project["rules"],
                    len(rows),
                    timestamp,
                    timestamp,
                ),
            )
            db.executemany(
                "INSERT INTO segments(task_id,block_ord,segment_ord,source) VALUES(?,?,?,?)", rows
            )
            db.execute(
                "INSERT INTO task_events(task_id,status,message,created_at) VALUES(?,?,?,?)",
                (task_id, "queued", "Задача добавлена в очередь.", timestamp),
            )
        return task_id

    def resume_task(self, task_id: str, model_id: str | None = None) -> dict[str, Any]:
        with _connect(self.db_path) as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            chosen_model = model_id or row["model_id"]
            if chosen_model != row["model_id"]:
                db.execute(
                    "UPDATE segments SET translation='', status='pending' WHERE task_id=?", (task_id,)
                )
                db.execute("UPDATE tasks SET completed=0 WHERE id=?", (task_id,))
            db.execute(
                "UPDATE tasks SET status='queued', model_id=?, error='', updated_at=? WHERE id=?",
                (chosen_model, _now(), task_id),
            )
            db.execute(
                "INSERT INTO task_events(task_id,status,message,created_at) VALUES(?,?,?,?)",
                (task_id, "queued", "Задача поставлена в очередь для продолжения.", _now()),
            )
        return dict(row)

    def set_task_status(self, task_id: str, status: str, message: str = "", error: str = "") -> None:
        with _connect(self.db_path) as db:
            db.execute(
                "UPDATE tasks SET status=?,error=?,updated_at=? WHERE id=?",
                (status, error, _now(), task_id),
            )
            db.execute(
                "INSERT INTO task_events(task_id,status,message,created_at) VALUES(?,?,?,?)",
                (task_id, status, message or error, _now()),
            )

    def pending_segments(self, task_id: str) -> list[dict[str, Any]]:
        with _connect(self.db_path) as db:
            rows = db.execute(
                "SELECT block_ord,segment_ord,source FROM segments WHERE task_id=? AND status!='complete' "
                "ORDER BY block_ord,segment_ord",
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def completed_segments(self, task_id: str, limit: int = 2) -> list[dict[str, Any]]:
        with _connect(self.db_path) as db:
            rows = db.execute(
                "SELECT block_ord,segment_ord,source,translation FROM segments "
                "WHERE task_id=? AND status='complete' ORDER BY block_ord DESC,segment_ord DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def save_segment(self, task_id: str, block_ord: int, segment_ord: int, translation: str) -> None:
        with _connect(self.db_path) as db:
            db.execute(
                "UPDATE segments SET translation=?,status='complete' WHERE task_id=? AND block_ord=? AND segment_ord=?",
                (translation, task_id, block_ord, segment_ord),
            )
            count = db.execute(
                "SELECT count(*) FROM segments WHERE task_id=? AND status='complete'", (task_id,)
            ).fetchone()[0]
            db.execute("UPDATE tasks SET completed=?,updated_at=? WHERE id=?", (count, _now(), task_id))

    def task(self, task_id: str) -> dict[str, Any]:
        with _connect(self.db_path) as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return dict(row)

    def tasks(self) -> list[dict[str, Any]]:
        with _connect(self.db_path) as db:
            rows = db.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def finish_task(self, task_id: str) -> None:
        with _connect(self.db_path) as db:
            task = db.execute(
                "SELECT document_id,target_lang,total FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            done = db.execute(
                "SELECT count(*) FROM segments WHERE task_id=? AND status='complete'", (task_id,)
            ).fetchone()[0]
            if task is None or done != task["total"]:
                raise RuntimeError("Нельзя завершить задачу с непереведёнными фрагментами.")
            rows = db.execute(
                "SELECT block_ord,segment_ord,translation FROM segments WHERE task_id=? AND status='complete' "
                "ORDER BY block_ord,segment_ord",
                (task_id,),
            ).fetchall()
            assembled: dict[int, list[str]] = {}
            for row in rows:
                assembled.setdefault(int(row["block_ord"]), []).append(row["translation"])
            timestamp = _now()
            for block_ord, parts in assembled.items():
                db.execute(
                    "INSERT INTO translations(document_id,target_lang,block_ord,text,edited,updated_at) "
                    "VALUES(?,?,?, ?,0,?) ON CONFLICT(document_id,target_lang,block_ord) "
                    "DO UPDATE SET text=excluded.text,edited=0,updated_at=excluded.updated_at",
                    (task["document_id"], task["target_lang"], block_ord, "".join(parts), timestamp),
                )
            db.execute(
                "UPDATE tasks SET status='complete',completed=total,error='',updated_at=? WHERE id=?",
                (timestamp, task_id),
            )
            db.execute(
                "INSERT INTO task_events(task_id,status,message,created_at) VALUES(?,?,?,?)",
                (task_id, "complete", "Перевод сохранён.", timestamp),
            )

    def translations(
        self,
        document_id: str,
        task_id: str | None = None,
        *,
        target_lang: str | None = None,
    ) -> dict[int, str]:
        with _connect(self.db_path) as db:
            if task_id is None:
                chosen_target = target_lang or db.execute(
                    "SELECT target_lang FROM project LIMIT 1"
                ).fetchone()["target_lang"]
                rows = db.execute(
                    "SELECT block_ord,text FROM translations WHERE document_id=? AND target_lang=?",
                    (document_id, chosen_target),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT block_ord,segment_ord,translation FROM segments WHERE task_id=? AND status='complete' "
                    "ORDER BY block_ord,segment_ord",
                    (task_id,),
                ).fetchall()
                grouped: dict[int, list[str]] = {}
                for row in rows:
                    grouped.setdefault(int(row["block_ord"]), []).append(row["translation"])
                return {block_ord: "".join(parts) for block_ord, parts in grouped.items()}
        return {int(row["block_ord"]): row["text"] for row in rows}

    def save_edit(
        self, document_id: str, block_ord: int, text: str, *, target_lang: str | None = None
    ) -> None:
        with _connect(self.db_path) as db:
            chosen_target = target_lang or db.execute(
                "SELECT target_lang FROM project LIMIT 1"
            ).fetchone()["target_lang"]
            db.execute(
                "INSERT INTO translations(document_id,target_lang,block_ord,text,edited,updated_at) "
                "VALUES(?,?,?,?,1,?) ON CONFLICT(document_id,target_lang,block_ord) "
                "DO UPDATE SET text=excluded.text,edited=1,updated_at=excluded.updated_at",
                (document_id, chosen_target, block_ord, text, _now()),
            )

    def task_events(self, task_id: str) -> list[dict[str, Any]]:
        with _connect(self.db_path) as db:
            rows = db.execute("SELECT * FROM task_events WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
        return [dict(row) for row in rows]

    def record_export(self, document_id: str, path: Path, *, target_lang: str | None = None) -> None:
        self.document(document_id)
        path = Path(path).resolve(strict=True)
        with _connect(self.db_path) as db:
            db.execute(
                "INSERT INTO exports(document_id,path,format,target_lang,created_at) VALUES(?,?,?,?,?)",
                (
                    document_id,
                    str(path),
                    path.suffix.lower().lstrip("."),
                    target_lang or self.project["target_lang"],
                    _now(),
                ),
            )

    def exports(self, document_id: str | None = None) -> list[dict[str, Any]]:
        with _connect(self.db_path) as db:
            if document_id is None:
                rows = db.execute("SELECT * FROM exports ORDER BY created_at DESC").fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM exports WHERE document_id=? ORDER BY created_at DESC", (document_id,)
                ).fetchall()
        return [dict(row) for row in rows]


def list_projects(base: Path) -> list[ProjectStore]:
    result = []
    if not Path(base).is_dir():
        return result
    for child in Path(base).iterdir():
        if (child / "project.sqlite").is_file():
            try:
                result.append(ProjectStore(child))
            except (OSError, sqlite3.DatabaseError):
                continue
    return sorted(result, key=lambda project: project.project["updated_at"], reverse=True)


def delete_project(base: Path, project_id: str) -> None:
    """Remove one project directory; never touch anything outside base."""
    base = Path(base).resolve()
    root = base / project_id
    if not root.is_dir():
        return
    if root.resolve().parent != base:
        raise ValueError("Каталог проекта находится вне хранилища проектов.")
    shutil.rmtree(root)
