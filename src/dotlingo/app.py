from __future__ import annotations

import argparse
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable

from dotlingo.formats import export_document
from dotlingo.hardware import HardwareSnapshot, assess_model, detect
from dotlingo.model_download import DownloadCancelled, download_model
from dotlingo.models import catalog, installed, model_path, verify_model
from dotlingo.paths import user_data_root
from dotlingo.preferences import load_preferences, save_preferences
from dotlingo.storage import ProjectStore, list_projects
from dotlingo.task_queue import TaskQueue, build_chunks
from dotlingo.theme import TOKENS

PAGES = (
    ("projects", "Проекты"),
    ("documents", "Документы"),
    ("queue", "Очередь"),
    ("review", "Проверка перевода"),
    ("models", "Модели"),
    ("glossary", "Глоссарии"),
    ("settings", "Настройки"),
)
LANGUAGES = {"en": "English", "ru": "Русский"}


def _format_size(size: int | None) -> str:
    if size is None:
        return "неизвестно"
    if size >= 1024**3:
        return f"{size / 1024**3:.2f} ГиБ ({size:,} байт)"
    return f"{size / 1024**2:.0f} МиБ"


class DotLingoApp:
    """Tk-only presentation layer; the core emits data and never touches widgets."""

    def __init__(self, root: tk.Tk, data_dir: Path | None = None, *, smoke: bool = False) -> None:
        self.root = root
        self.data_dir = Path(data_dir) if data_dir else user_data_root()
        self.projects_dir = self.data_dir / "projects"
        self.models_dir = self.data_dir / "models"
        self.preferences_root = self.data_dir
        self.events: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self.projects: list[ProjectStore] = list_projects(self.projects_dir)
        self.project_queues: dict[str, TaskQueue] = {}
        self.active: ProjectStore | None = None
        self.page = "projects"
        self.hardware: HardwareSnapshot | None = None
        self.reduce_motion = bool(load_preferences(self.preferences_root)["reduce_motion"])
        self._selected_doc = ""
        self._review_order: list[int] = []
        self._review_block: int | None = None
        self._review_text_original = ""
        self._review_loading = False
        self._model_checks: dict[str, tk.BooleanVar] = {}
        self._download_cancel: threading.Event | None = None
        self._download_window: tk.Toplevel | None = None
        self._closing = False

        self.root.title("DotLingo · локальный перевод документов")
        self.root.geometry("1160x760")
        self.root.minsize(760, 540)
        self._set_icon()
        self._style()
        self._build_shell()
        self._refresh_project_list()
        self._restore_active_project()
        self._poll_events()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        if smoke:
            self.root.withdraw()
            self.root.after(80, self._smoke_check)
        elif not load_preferences(self.preferences_root)["setup_seen"]:
            self.root.after(250, self.open_setup_wizard)

        # A queued task represents an explicit action from an earlier session. Resume those
        # durable queue entries; interrupted/failed/cancelled tasks require a deliberate click.
        for project in self.projects:
            if any(task["status"] == "queued" for task in project.tasks()):
                self._queue_for(project).start()

    def _set_icon(self) -> None:
        candidate = Path(__file__).parent / "assets" / "app_icon.ico"
        if candidate.is_file():
            try:
                self.root.iconbitmap(str(candidate))
            except tk.TclError:
                pass

    def _style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        colors = TOKENS
        self.root.configure(background=colors["bg"])
        style.configure("TFrame", background=colors["bg"])
        style.configure("Panel.TFrame", background=colors["surface"])
        style.configure("Raised.TFrame", background=colors["surface_raised"])
        style.configure("TLabel", background=colors["bg"], foreground=colors["text"], font=(colors["font"], colors["font_body"]))
        style.configure("Muted.TLabel", background=colors["bg"], foreground=colors["muted"], font=(colors["font"], colors["font_small"]))
        style.configure("Panel.TLabel", background=colors["surface"], foreground=colors["text"], font=(colors["font"], colors["font_body"]))
        style.configure("Title.TLabel", background=colors["bg"], foreground=colors["text"], font=(colors["font"], colors["font_title"], "bold"))
        style.configure("Section.TLabel", background=colors["bg"], foreground=colors["text"], font=(colors["font"], colors["font_heading"], "bold"))
        style.configure("TButton", background=colors["surface_raised"], foreground=colors["text"], padding=(10, 7), bordercolor=colors["border"])
        style.map("TButton", background=[("active", colors["surface_hover"]), ("pressed", colors["accent_dim"])], foreground=[("disabled", colors["muted"])])
        style.configure("Accent.TButton", background=colors["accent_dim"], foreground=colors["text"], padding=(11, 8), bordercolor=colors["accent"])
        style.map("Accent.TButton", background=[("active", colors["accent"]), ("pressed", colors["accent_dim"])])
        style.configure("Nav.TButton", background=colors["bg"], foreground=colors["muted"], anchor="w", padding=(12, 9), borderwidth=0)
        style.map("Nav.TButton", background=[("active", colors["surface_hover"]), ("pressed", colors["accent_dim"])], foreground=[("active", colors["text"])])
        style.configure("Treeview", background=colors["surface"], fieldbackground=colors["surface"], foreground=colors["text"], rowheight=27, bordercolor=colors["border"])
        style.map("Treeview", background=[("selected", colors["highlight"])], foreground=[("selected", colors["text"])])
        style.configure("Treeview.Heading", background=colors["surface_raised"], foreground=colors["text"], padding=6)
        style.configure("TNotebook", background=colors["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=colors["surface"], foreground=colors["muted"], padding=(10, 7))
        style.map("TNotebook.Tab", background=[("selected", colors["surface_raised"])], foreground=[("selected", colors["text"])])
        style.configure("Horizontal.TProgressbar", troughcolor=colors["surface_raised"], background=colors["accent"], bordercolor=colors["border"])

    def _build_shell(self) -> None:
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        self.sidebar = ttk.Frame(outer, width=196, style="Panel.TFrame", padding=(10, 15))
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.grid_propagate(False)
        brand = ttk.Label(self.sidebar, text=".ядро  DotLingo", style="Panel.TLabel", font=(TOKENS["font"], 13, "bold"))
        brand.pack(anchor="w", padx=8, pady=(3, 18))
        self.nav_buttons: dict[str, ttk.Button] = {}
        for key, label in PAGES:
            if key in {"models", "glossary", "settings"}:
                ttk.Separator(self.sidebar).pack(fill="x", padx=8, pady=8)
            button = ttk.Button(self.sidebar, text=label, style="Nav.TButton", command=lambda page=key: self.show_page(page))
            button.pack(fill="x", pady=1)
            button.configure(takefocus=True)
            self.nav_buttons[key] = button
        self.sidebar_status = ttk.Label(self.sidebar, text="Локальная работа · без облака", style="Muted.TLabel", wraplength=160)
        self.sidebar_status.pack(side="bottom", anchor="w", padx=8, pady=8)

        workspace = ttk.Frame(outer, padding=(18, 12, 18, 18))
        workspace.grid(row=0, column=1, sticky="nsew")
        workspace.columnconfigure(0, weight=1)
        workspace.rowconfigure(1, weight=1)
        self.project_header = ttk.Frame(workspace)
        self.project_header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        self.project_header.columnconfigure(0, weight=1)
        self.header_title = ttk.Label(self.project_header, text="Проекты", style="Title.TLabel")
        self.header_title.grid(row=0, column=0, sticky="w")
        self.header_project = ttk.Label(self.project_header, text="Проект не выбран", style="Muted.TLabel")
        self.header_project.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.content = ttk.Frame(workspace)
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)
        self.show_page("projects")

    def _panel(self, parent: tk.Misc, **kwargs: Any) -> ttk.Frame:
        return ttk.Frame(parent, style="Panel.TFrame", padding=kwargs.pop("padding", 14), **kwargs)

    def _heading(self, parent: tk.Misc, text: str) -> ttk.Label:
        return ttk.Label(parent, text=text, style="Section.TLabel")

    def _text_widget(self, parent: tk.Misc, *, wrap: str = "word", height: int = 5, disabled: bool = False) -> tk.Text:
        widget = tk.Text(
            parent,
            height=height,
            wrap=wrap,
            undo=True,
            background=TOKENS["surface_raised"],
            foreground=TOKENS["text"],
            insertbackground=TOKENS["accent"],
            selectbackground=TOKENS["highlight"],
            relief="flat",
            padx=10,
            pady=9,
            font=(TOKENS["font"], TOKENS["font_body"]),
            highlightthickness=1,
            highlightbackground=TOKENS["border"],
            highlightcolor=TOKENS["border_focus"],
            takefocus=True,
        )
        if disabled:
            widget.configure(state="disabled")
        return widget

    def _clear_content(self) -> None:
        for child in self.content.winfo_children():
            child.destroy()

    def show_page(self, page: str) -> None:
        if self.page == "review" and not self._confirm_review_change():
            return
        self.page = page
        labels = dict(PAGES)
        self.header_title.configure(text=labels[page])
        self.header_project.configure(text=self.active.project["title"] if self.active else "Проект не выбран")
        self._clear_content()
        renderers = {
            "projects": self._show_projects,
            "documents": self._show_documents,
            "queue": self._show_queue,
            "review": self._show_review,
            "models": self._show_models,
            "glossary": self._show_glossary,
            "settings": self._show_settings,
        }
        renderers[page]()

    def _active_store(self) -> ProjectStore | None:
        return self.active

    def _refresh_project_list(self) -> None:
        self.projects = list_projects(self.projects_dir)

    def _restore_active_project(self) -> None:
        last = load_preferences(self.preferences_root).get("last_project", "")
        self.active = next((project for project in self.projects if project.project["id"] == last), None)
        if self.active is None and self.projects:
            self.active = self.projects[0]
        self.header_project.configure(text=self.active.project["title"] if self.active else "Проект не выбран")

    def _select_project(self, project: ProjectStore) -> None:
        self.active = project
        self._selected_doc = ""
        preferences = load_preferences(self.preferences_root)
        preferences["last_project"] = project.project["id"]
        save_preferences(preferences, self.preferences_root)
        self.header_project.configure(text=project.project["title"])
        self.show_page("documents")

    def _show_projects(self) -> None:
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)
        panel = self._panel(self.content)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        top = ttk.Frame(panel, style="Panel.TFrame")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        top.columnconfigure(0, weight=1)
        ttk.Label(top, text="Рабочие проекты", style="Panel.TLabel", font=(TOKENS["font"], 12, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Button(top, text="Новый проект", style="Accent.TButton", command=self._new_project).grid(row=0, column=1, sticky="e")
        tree = ttk.Treeview(panel, columns=("direction", "documents", "updated"), show="headings", selectmode="browse")
        tree.heading("direction", text="Языки")
        tree.heading("documents", text="Документы")
        tree.heading("updated", text="Изменён")
        tree.column("direction", width=130, stretch=False)
        tree.column("documents", width=110, stretch=False)
        tree.column("updated", width=170, stretch=False)
        tree.grid(row=1, column=0, sticky="nsew")
        tree.bind("<Double-1>", lambda _: self._open_tree_project(tree))
        for project in self.projects:
            info = project.project
            tree.insert("", "end", iid=info["id"], values=(f"{info['source_lang']} → {info['target_lang']}", len(project.documents()), info["updated_at"][:16].replace("T", " ")), text=info["title"])
        if not self.projects:
            ttk.Label(panel, text="Создайте проект, затем добавьте TXT, Markdown, DOCX или EPUB. PDF поддерживается только с текстовым слоем.", style="Muted.TLabel", wraplength=620).grid(row=2, column=0, sticky="w", pady=14)
        ttk.Button(panel, text="Открыть выбранный проект", command=lambda: self._open_tree_project(tree)).grid(row=3, column=0, sticky="e", pady=(12, 0))

    def _open_tree_project(self, tree: ttk.Treeview) -> None:
        selection = tree.selection()
        if not selection:
            messagebox.showinfo("Выберите проект", "Выберите проект в списке.", parent=self.root)
            return
        chosen = next((item for item in self.projects if item.project["id"] == selection[0]), None)
        if chosen:
            self._select_project(chosen)

    def _new_project(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Новый проект")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(background=TOKENS["bg"])
        frame = self._panel(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Название проекта", style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        title = ttk.Entry(frame, width=34)
        title.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(frame, text="Языки перевода", style="Panel.TLabel").grid(row=2, column=0, sticky="w", pady=(0, 6))
        languages = list(LANGUAGES)
        direction = ttk.Frame(frame, style="Panel.TFrame")
        direction.grid(row=3, column=0, sticky="ew", pady=(0, 16))
        source = tk.StringVar(value="en")
        target = tk.StringVar(value="ru")
        ttk.Combobox(direction, values=languages, textvariable=source, state="readonly", width=8).pack(side="left")
        ttk.Label(direction, text=" → ", style="Panel.TLabel").pack(side="left")
        ttk.Combobox(direction, values=languages, textvariable=target, state="readonly", width=8).pack(side="left")

        def create() -> None:
            if source.get() == target.get():
                messagebox.showerror("Языки совпадают", "Выберите разные исходный и целевой языки.", parent=dialog)
                return
            project = ProjectStore.create(self.projects_dir, title.get().strip(), source.get(), target.get())
            self._refresh_project_list()
            self._select_project(project)
            dialog.destroy()

        ttk.Button(frame, text="Создать проект", style="Accent.TButton", command=create).grid(row=4, column=0, sticky="e")
        title.focus_set()
        dialog.bind("<Return>", lambda _: create())

    def _show_documents(self) -> None:
        store = self._active_store()
        if not store:
            self._need_project()
            return
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)
        panel = self._panel(self.content)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        head = ttk.Frame(panel, style="Panel.TFrame")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text="Исходники хранятся отдельно и не изменяются при переводе.", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(head, text="Добавить файлы", command=self._import_files).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(head, text="Перевести выбранные", style="Accent.TButton", command=self._enqueue_selected_documents).grid(row=0, column=2, padx=(8, 0))
        tree = ttk.Treeview(panel, columns=("format", "hash", "status", "exports"), show="tree headings", selectmode="extended")
        tree.heading("#0", text="Документ")
        tree.heading("format", text="Формат")
        tree.heading("hash", text="SHA-256 оригинала")
        tree.heading("status", text="Перевод")
        tree.heading("exports", text="Результаты")
        tree.column("#0", width=260)
        tree.column("format", width=80, stretch=False)
        tree.column("hash", width=230)
        tree.column("status", width=120, stretch=False)
        tree.column("exports", width=95, stretch=False)
        tree.grid(row=1, column=0, sticky="nsew")
        tree.bind("<Double-1>", lambda _: self._open_selected_document(tree))
        tree.bind("<<TreeviewSelect>>", lambda _: self._set_selected_doc(tree))
        for document in store.documents():
            translations = store.translations(document.id)
            translated_count = len(translations)
            total = sum(1 for block in store.blocks(document.id) if block.translatable and block.text.strip())
            status = f"{translated_count}/{total} блоков" if translated_count else "не переведён"
            export_count = len(store.exports(document.id))
            tree.insert("", "end", iid=document.id, text=document.name, values=(document.format.upper(), document.sha256[:16] + "…", status, f"{export_count} файлов"))
        if not store.documents():
            ttk.Label(panel, text="Добавьте книгу или документ. Ограничения экспорта для каждого формата будут показаны до запуска.", style="Muted.TLabel", wraplength=650).grid(row=2, column=0, sticky="w", pady=14)
        ttk.Button(panel, text="Проверить выбранный перевод", command=lambda: self._open_selected_document(tree)).grid(row=3, column=0, sticky="e", pady=(10, 0))
        ttk.Button(panel, text="Открыть последний экспорт", command=self._open_latest_export).grid(row=3, column=0, sticky="w", pady=(10, 0))

    def _set_selected_doc(self, tree: ttk.Treeview) -> None:
        selection = tree.selection()
        self._selected_doc = selection[0] if selection else ""

    def _open_latest_export(self) -> None:
        store = self._active_store()
        if not store or not self._selected_doc:
            messagebox.showinfo("Выберите документ", "Выберите документ с экспортированным результатом.", parent=self.root)
            return
        exports = store.exports(self._selected_doc)
        if not exports:
            messagebox.showinfo("Экспортов нет", "Экспортируйте перевод, чтобы сохранить результат в проекте.", parent=self.root)
            return
        path = Path(exports[0]["path"])
        if not path.is_file():
            messagebox.showerror("Файл не найден", "Последний экспорт перемещён или удалён за пределами приложения.", parent=self.root)
            return
        try:
            startfile = getattr(__import__("os"), "startfile", None)
            if startfile:
                startfile(str(path))
            else:
                webbrowser.open(path.as_uri())
        except OSError as exc:
            messagebox.showerror("Не удалось открыть файл", str(exc), parent=self.root)

    def _open_selected_document(self, tree: ttk.Treeview) -> None:
        self._set_selected_doc(tree)
        if not self._selected_doc:
            messagebox.showinfo("Выберите документ", "Выберите строку документа.", parent=self.root)
            return
        self.show_page("review")

    def _import_files(self) -> None:
        store = self._active_store()
        if not store:
            return
        paths = filedialog.askopenfilenames(
            parent=self.root,
            title="Добавить документы",
            filetypes=[("Поддерживаемые документы", "*.txt *.md *.markdown *.docx *.epub *.pdf"), ("Все файлы", "*.*")],
        )
        if not paths:
            return
        self._set_busy(True, "Импорт документов…")

        def work() -> list[tuple[str, str]]:
            outcomes: list[tuple[str, str]] = []
            for name in paths:
                try:
                    doc = store.import_file(Path(name))
                    warning = "\n".join(doc.warnings)
                    outcomes.append((doc.name, warning))
                except Exception as exc:
                    outcomes.append((Path(name).name, f"ОШИБКА: {exc}"))
            return outcomes

        self._submit("import", work, lambda result: self._import_done(result))

    def _import_done(self, result: list[tuple[str, str]]) -> None:
        self._set_busy(False)
        errors = [f"{name}: {warning[8:]}" for name, warning in result if warning.startswith("ОШИБКА:")]
        warnings = [f"{name}: {warning}" for name, warning in result if warning and not warning.startswith("ОШИБКА:")]
        if errors or warnings:
            text = "\n\n".join((errors + warnings)[:8])
            messagebox.showwarning("Результат импорта", text, parent=self.root)
        self._refresh_project_list()
        self.show_page("documents")

    def _enqueue_selected_documents(self) -> None:
        store = self._active_store()
        if not store:
            return
        model_id = store.project.get("model_id") or "qwen3-1.7b-q8"
        model = next((item for item in catalog() if item["id"] == model_id), None)
        if model is None or not installed(model, self.models_dir):
            messagebox.showwarning("Модель не установлена", "Сначала установите выбранную модель на странице «Модели».", parent=self.root)
            return
        if not self._runtime_available():
            messagebox.showerror("Локальный runtime недоступен", "В этой сборке не найден llama-cpp-python. Установка веса модели не добавляет runtime. См. docs/BUILD_WINDOWS.md.", parent=self.root)
            return
        documents = [document for document in store.documents() if document.id in self._selected_document_ids()]
        if not documents:
            messagebox.showinfo("Выберите документы", "Выберите один или несколько документов.", parent=self.root)
            return
        warning_lines = []
        for document in documents:
            warning_lines.extend(f"• {document.name}: {warning}" for warning in document.warnings)
        summary = f"В очередь добавится {len(documents)} документ(ов). ETA не рассчитывается."
        if warning_lines:
            summary += "\n\nОграничения формата:\n" + "\n".join(warning_lines[:8])
        summary += "\n\nЗапустить локальный перевод сейчас?"
        if not messagebox.askyesno("Подтверждение перевода", summary, parent=self.root):
            return
        task_queue = self._queue_for(store)
        try:
            for document in documents:
                task_id = store.create_task(document.id, model_id, build_chunks(store.blocks(document.id)))
                task_queue.enqueue(task_id)
        except Exception as exc:
            messagebox.showerror("Не удалось поставить задачу", str(exc), parent=self.root)
        self.show_page("queue")

    def _selected_document_ids(self) -> set[str]:
        # The selected ids are copied from the currently displayed document tree.
        tree = next((item for item in self.content.winfo_children() if isinstance(item, ttk.Frame)), None)
        if tree:
            for widget in tree.winfo_children():
                if isinstance(widget, ttk.Treeview) and "format" in widget["columns"]:
                    return set(widget.selection())
        return {self._selected_doc} if self._selected_doc else set()

    def _show_queue(self) -> None:
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)
        panel = self._panel(self.content)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        ttk.Label(panel, text="Прогресс показывает готовые фрагменты. Время до завершения не оценивается.", style="Panel.TLabel", wraplength=720).grid(row=0, column=0, sticky="w", pady=(0, 10))
        columns = ("document", "status", "progress", "model", "error")
        tree = ttk.Treeview(panel, columns=columns, show="headings", selectmode="browse")
        for key, label in zip(columns, ("Документ", "Статус", "Фрагменты", "Модель", "Сообщение")):
            tree.heading(key, text=label)
        tree.column("document", width=185)
        tree.column("status", width=110, stretch=False)
        tree.column("progress", width=95, stretch=False)
        tree.column("model", width=145)
        tree.column("error", width=310)
        tree.grid(row=1, column=0, sticky="nsew")
        tasks: list[tuple[ProjectStore, dict[str, Any]]] = []
        for project in self.projects:
            docs = {doc.id: doc.name for doc in project.documents()}
            tasks.extend((project, task) for task in project.tasks())
            for task in project.tasks():
                task_id = task["id"]
                model_title = next((item["name"] for item in catalog() if item["id"] == task["model_id"]), task["model_id"])
                tree.insert("", "end", iid=task_id, values=(docs.get(task["document_id"], "Документ"), self._status_label(task["status"]), f"{task['completed']}/{task['total']}", model_title, task["error"] or ""))
        button_row = ttk.Frame(panel, style="Panel.TFrame")
        button_row.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(button_row, text="Продолжить / повторить", command=lambda: self._queue_action(tree, "resume")).pack(side="left")
        ttk.Button(button_row, text="Пауза", command=lambda: self._queue_action(tree, "pause")).pack(side="left", padx=7)
        ttk.Button(button_row, text="Отменить", command=lambda: self._queue_action(tree, "cancel")).pack(side="left")
        ttk.Button(button_row, text="Обновить", command=lambda: self.show_page("queue")).pack(side="right")
        self._queue_tree = tree

    @staticmethod
    def _status_label(status: str) -> str:
        return {
            "queued": "В очереди", "running": "Перевод", "paused": "Пауза", "complete": "Готово",
            "cancelled": "Отменено", "interrupted": "Прервано", "failed": "Ошибка",
        }.get(status, status)

    def _queue_action(self, tree: ttk.Treeview, action: str) -> None:
        selection = tree.selection()
        if not selection:
            messagebox.showinfo("Выберите задачу", "Выберите задачу в очереди.", parent=self.root)
            return
        task_id = selection[0]
        for project in self.projects:
            try:
                task = project.task(task_id)
            except KeyError:
                continue
            queue_for_project = self._queue_for(project)
            if action == "resume":
                if task["status"] == "complete":
                    messagebox.showinfo("Перевод готов", "Чтобы начать новый перевод, выберите документ на странице «Документы».", parent=self.root)
                    return
                queue_for_project.resume(task_id)
            elif action == "pause":
                queue_for_project.pause(task_id)
            else:
                queue_for_project.cancel(task_id)
            self.show_page("queue")
            return

    def _show_review(self) -> None:
        store = self._active_store()
        if not store:
            self._need_project()
            return
        document = None
        if self._selected_doc:
            try:
                document = store.document(self._selected_doc)
            except KeyError:
                pass
        if document is None:
            docs = store.documents()
            document = docs[0] if docs else None
        if not document:
            ttk.Label(self.content, text="Добавьте документ, чтобы начать проверку.").grid(row=0, column=0, sticky="nw")
            return
        self._selected_doc = document.id
        parsed = store.parsed(document.id)
        blocks = [block for block in parsed.blocks if block.translatable]
        translated = store.translations(document.id)
        complete = sum(1 for block in blocks if block.order in translated)
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(self.content)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        toolbar.columnconfigure(1, weight=1)
        ttk.Label(toolbar, text=document.name, style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(toolbar, text=f"Переведено блоков: {complete}/{len(blocks)}", style="Muted.TLabel").grid(row=0, column=1, sticky="e", padx=10)
        ttk.Button(toolbar, text="Сохранить правку", command=self._save_review_edit).grid(row=0, column=2, padx=4)
        ttk.Button(toolbar, text="Экспорт…", style="Accent.TButton", command=self._export_current).grid(row=0, column=3)

        body = ttk.Panedwindow(self.content, orient="horizontal")
        body.grid(row=1, column=0, sticky="nsew")
        navigation = self._panel(body, padding=8)
        navigation.columnconfigure(0, weight=1)
        navigation.rowconfigure(1, weight=1)
        ttk.Label(navigation, text="Поиск по документу", style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 5))
        search = ttk.Entry(navigation)
        search.grid(row=0, column=0, sticky="ew", pady=(22, 6))
        tree = ttk.Treeview(navigation, show="tree", selectmode="browse")
        tree.grid(row=1, column=0, sticky="nsew")
        translation_area = ttk.Frame(body)
        translation_area.columnconfigure(0, weight=1)
        translation_area.columnconfigure(1, weight=1)
        translation_area.rowconfigure(1, weight=1)
        ttk.Label(translation_area, text="Оригинал", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6), padx=(0, 8))
        ttk.Label(translation_area, text="Перевод · можно редактировать", style="Section.TLabel").grid(row=0, column=1, sticky="w", pady=(0, 6), padx=(8, 0))
        source_text = self._text_widget(translation_area, height=15, disabled=True)
        target_text = self._text_widget(translation_area, height=15)
        source_text.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
        target_text.grid(row=1, column=1, sticky="nsew", padx=(5, 0))
        self._review_target = target_text
        self._review_source = source_text
        self._review_tree = tree
        self._review_block = None

        groups: dict[str, list[Any]] = {}
        for block in blocks:
            groups.setdefault(block.section, []).append(block)

        def fill_tree(query: str = "") -> None:
            tree.delete(*tree.get_children())
            self._review_order = []
            lowered = query.casefold().strip()
            if lowered:
                parent = tree.insert("", "end", text="Результаты поиска", open=True)
                matches = [block for block in blocks if lowered in block.text.casefold() or lowered in translated.get(block.order, "").casefold()]
                for block in matches:
                    self._review_order.append(block.order)
                    tree.insert(parent, "end", iid=f"b{block.order}", text=f"{block.section_title} · {block.text.strip()[:48]}")
            else:
                for section, items in groups.items():
                    title = items[0].section_title if items else section
                    parent = tree.insert("", "end", iid=f"s{section}", text=title or "Раздел", open=True)
                    for block in items:
                        self._review_order.append(block.order)
                        preview = block.text.strip().replace("\n", " ")[:48]
                        tree.insert(parent, "end", iid=f"b{block.order}", text=preview or f"Блок {block.order + 1}")
            first = next((item for item in tree.get_children("") if tree.get_children(item)), None)
            if first:
                child = tree.get_children(first)[0]
                tree.selection_set(child)
                tree.focus(child)
                select_block(child)

        def select_block(iid: str) -> None:
            if not iid.startswith("b"):
                return
            try:
                order = int(iid[1:])
            except ValueError:
                return
            if self._review_block is not None and self._review_target.edit_modified():
                old = self._review_target.get("1.0", "end-1c")
                if old != self._review_text_original:
                    decision = messagebox.askyesnocancel("Несохранённая правка", "Сохранить изменения перевода перед переходом?", parent=self.root)
                    if decision is None:
                        old_iid = f"b{self._review_block}"
                        if tree.exists(old_iid):
                            tree.selection_set(old_iid)
                            tree.focus(old_iid)
                        return
                    if decision:
                        self._save_review_edit()
                    else:
                        self._review_target.edit_modified(False)
            block = next((item for item in blocks if item.order == order), None)
            if not block:
                return
            self._review_block = order
            self._review_loading = True
            source_text.configure(state="normal")
            source_text.delete("1.0", "end")
            source_text.insert("1.0", block.text)
            source_text.configure(state="disabled")
            target_text.delete("1.0", "end")
            current = store.translations(document.id).get(order, "")
            target_text.insert("1.0", current)
            self._review_text_original = current
            target_text.edit_modified(False)
            self._review_loading = False

        tree.bind("<<TreeviewSelect>>", lambda _: select_block(tree.selection()[0]) if tree.selection() else None)
        search.bind("<KeyRelease>", lambda _: fill_tree(search.get()))
        body.add(navigation, weight=1)
        body.add(translation_area, weight=4)
        fill_tree()

    def _confirm_review_change(self) -> bool:
        target = getattr(self, "_review_target", None)
        if not target or not target.winfo_exists() or self._review_block is None or not target.edit_modified():
            return True
        text = target.get("1.0", "end-1c")
        if text == self._review_text_original:
            return True
        decision = messagebox.askyesnocancel("Несохранённая правка", "Сохранить изменения перевода?", parent=self.root)
        if decision is None:
            return False
        if decision:
            self._save_review_edit()
        else:
            target.edit_modified(False)
        return True

    def _save_review_edit(self) -> None:
        store = self._active_store()
        if not store or self._review_block is None or not hasattr(self, "_review_target"):
            return
        text = self._review_target.get("1.0", "end-1c")
        store.save_edit(self._selected_doc, self._review_block, text)
        self._review_text_original = text
        self._review_target.edit_modified(False)
        self.sidebar_status.configure(text="Правка перевода сохранена")

    def _export_current(self) -> None:
        store = self._active_store()
        if not store or not self._selected_doc:
            return
        document = store.document(self._selected_doc)
        try:
            store.verify_source(document.id)
        except OSError as exc:
            messagebox.showerror("Оригинал изменён", str(exc), parent=self.root)
            return
        parsed = store.parsed(document.id)
        allowed = {
            "txt": (".txt", ".md"), "markdown": (".md", ".txt"), "docx": (".docx", ".txt", ".md"),
            "epub": (".epub", ".txt", ".md"), "pdf": (".txt", ".md"),
        }.get(parsed.format, (".txt",))
        extension = allowed[0]
        destination = filedialog.asksaveasfilename(
            parent=self.root,
            title="Экспорт перевода",
            initialdir=str(store.root / "output"),
            initialfile=f"{document.source_path.stem}.translated{extension}",
            defaultextension=extension,
            filetypes=[("Поддерживаемый экспорт", " ".join(f"*{suffix}" for suffix in allowed))],
        )
        if not destination:
            return
        translations = store.translations(document.id)
        missing = sum(1 for block in parsed.blocks if block.translatable and block.text.strip() and block.order not in translations)
        if missing and not messagebox.askyesno("Перевод не завершён", f"Для {missing} блоков пока нет перевода. Экспортёр оставит их на исходном языке. Продолжить?", parent=self.root):
            return
        if Path(destination).suffix.lower() not in allowed:
            messagebox.showerror("Формат не поддерживается", "Выбранное расширение недоступно для этого исходного документа.", parent=self.root)
            return
        try:
            export_document(document.source_path, Path(destination), parsed, translations)
            store.record_export(document.id, Path(destination))
            messagebox.showinfo("Экспорт готов", f"Файл сохранён отдельно:\n{destination}", parent=self.root)
        except Exception as exc:
            messagebox.showerror("Ошибка экспорта", str(exc), parent=self.root)

    def _show_models(self) -> None:
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(1, weight=1)
        top = ttk.Frame(self.content)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        top.columnconfigure(0, weight=1)
        ttk.Label(top, text="Вес модели и runtime устанавливаются отдельно. Выберите модель явно; автозагрузка отключена.", style="Muted.TLabel", wraplength=700).grid(row=0, column=0, sticky="w")
        ttk.Button(top, text="Обновить данные устройства", command=self._detect_hardware).grid(row=0, column=1, sticky="e")
        container = ttk.Frame(self.content)
        container.grid(row=1, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        canvas = tk.Canvas(container, background=TOKENS["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        cards = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=cards, anchor="nw")
        cards.columnconfigure(0, weight=1)
        cards.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        self._model_checks = {}
        snapshot = self.hardware
        if snapshot is None:
            ttk.Label(cards, text="Проверка устройства ещё не запускалась.", style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=12)
        for index, model in enumerate(catalog()):
            card = self._panel(cards)
            card.grid(row=index + 1, column=0, sticky="ew", pady=(0, 10))
            card.columnconfigure(0, weight=1)
            ttk.Label(card, text=model["name"], style="Panel.TLabel", font=(TOKENS["font"], 12, "bold")).grid(row=0, column=0, sticky="w")
            quantization = model.get("quantization") or "не закреплено"
            license_line = f"Лицензия: {model['license']} · {model['format']} / {quantization} · {_format_size(model.get('size_bytes'))}"
            ttk.Label(card, text=license_line, style="Panel.TLabel", wraplength=780).grid(row=1, column=0, sticky="w", pady=(4, 0))
            requirement = "RAM для запуска: не опубликована; оценка DotLingo отсутствует"
            if model.get("estimated_ram_gb") is not None:
                requirement = f"Оценка RAM: около {model['estimated_ram_gb']:.1f} ГБ при контексте 4096; значение расчётное, не измерено"
            compatibility = "Совместимость: не удалось определить"
            if snapshot:
                result, reason = assess_model(snapshot, model) if model.get("estimated_ram_gb") is not None and model.get("status") == "available" else ("unknown", "Профиль памяти/runtime этой модели не проверен.")
                compatibility = f"Устройство: {snapshot.profile}. {reason}"
            installed_now = installed(model, self.models_dir)
            run_state = "Вес установлен; SHA-256 проверяется перед запуском задачи" if installed_now else "Вес не установлен"
            if not model.get("tested_on_windows"):
                run_state += " · точный Windows запуск ещё не проверен"
            ttk.Label(card, text=f"{requirement}\n{compatibility}\nСостояние: {run_state}\nПары языков: {model['languages']}", style="Muted.TLabel", wraplength=790, justify="left").grid(row=2, column=0, sticky="w", pady=6)
            ttk.Label(card, text=model.get("notes", ""), style="Muted.TLabel", wraplength=790, justify="left").grid(row=3, column=0, sticky="w", pady=(0, 5))
            action = ttk.Frame(card, style="Panel.TFrame")
            action.grid(row=4, column=0, sticky="ew")
            ttk.Button(action, text="Лицензия и карточка", command=lambda url=model["license_url"]: webbrowser.open(url)).pack(side="left")
            if model.get("status") == "available":
                self._model_checks[model["id"]] = tk.BooleanVar(value=False)
                ttk.Checkbutton(action, text=f"Я ознакомился с лицензией {model['license']} и согласен скачать {model['name']} объёмом {_format_size(model['size_bytes'])}", variable=self._model_checks[model["id"]]).pack(side="left", padx=8)
                text = "Проверить модель" if installed_now else "Скачать модель"
                ttk.Button(action, text=text, style="Accent.TButton", command=lambda item=model: self._consent_and_download(item)).pack(side="right")
            else:
                ttk.Label(action, text="Загрузка отключена до проверки runtime", style="Muted.TLabel").pack(side="right")
        self.root.after_idle(lambda: canvas.configure(scrollregion=canvas.bbox("all")))

    def _consent_and_download(self, model: dict[str, Any]) -> None:
        if installed(model, self.models_dir):
            self._submit(
                "model verification",
                lambda: verify_model(model_path(model, self.models_dir), model),
                lambda result: self._model_verification_done(result),
            )
            return
        consent = self._model_checks.get(model["id"])
        if not consent or not consent.get():
            messagebox.showwarning("Нужно согласие", "Прочитайте лицензию и отметьте отдельное согласие перед загрузкой веса.", parent=self.root)
            return
        if model.get("status") != "available":
            return
        result = messagebox.askyesno("Подтвердите загрузку модели", f"Файл: {model['filename']}\nРазмер: {_format_size(model['size_bytes'])}\nЛицензия: {model['license']}\n\nИсточник: {model['repo']}\nРевизия: {model['revision']}\nSHA-256 проверяется после скачивания.\n\nСкачать сейчас?", parent=self.root)
        if not result:
            return
        self._begin_download(model)

    def _model_verification_done(self, result: Any) -> None:
        if isinstance(result, Exception):
            messagebox.showerror("Проверка модели не пройдена", str(result), parent=self.root)
        else:
            messagebox.showinfo("Модель проверена", "Размер, GGUF и SHA-256 совпадают с реестром.", parent=self.root)
        self.show_page("models")

    def _begin_download(self, model: dict[str, Any]) -> None:
        cancel = threading.Event()
        self._download_cancel = cancel
        window = tk.Toplevel(self.root)
        window.title("Загрузка модели")
        window.transient(self.root)
        window.configure(background=TOKENS["bg"])
        window.protocol("WM_DELETE_WINDOW", lambda: cancel.set())
        panel = self._panel(window, padding=18)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text=f"Загружается {model['name']}", style="Panel.TLabel", font=(TOKENS["font"], 12, "bold")).pack(anchor="w")
        label = ttk.Label(panel, text=f"Получено 0 из {_format_size(model['size_bytes'])} · ETA не показывается", style="Panel.TLabel")
        label.pack(anchor="w", pady=9)
        progress = ttk.Progressbar(panel, mode="determinate", maximum=100)
        progress.pack(fill="x", pady=(2, 10))
        ttk.Button(panel, text="Отменить и продолжить позже", command=cancel.set).pack(anchor="e")
        self._download_window = window

        def progress_event(value: dict[str, Any]) -> None:
            self.events.put(("download_progress", value))

        def work() -> Path:
            return download_model(model, self.models_dir, cancel=cancel, on_progress=progress_event)

        self._submit("download", work, lambda result: self._download_done(model, result))
        self._download_progress_widgets = (label, progress)

    def _download_done(self, model: dict[str, Any], result: Any) -> None:
        if self._download_window and self._download_window.winfo_exists():
            self._download_window.destroy()
        self._download_window = None
        self._download_cancel = None
        if isinstance(result, Exception):
            title = "Загрузка отменена" if isinstance(result, DownloadCancelled) else "Ошибка загрузки"
            messagebox.showwarning(title, str(result), parent=self.root)
        else:
            messagebox.showinfo("Модель готова", f"SHA-256 и формат проверены. Файл активирован:\n{result}", parent=self.root)
        self.show_page("models")

    def _show_glossary(self) -> None:
        store = self._active_store()
        if not store:
            self._need_project()
            return
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(1, weight=1)
        info = ttk.Label(self.content, text=f"Глоссарий проекта · {store.project['source_lang']} → {store.project['target_lang']}", style="Muted.TLabel")
        info.grid(row=0, column=0, sticky="w", pady=(0, 10))
        tree = ttk.Treeview(self.content, columns=("target",), show="headings", selectmode="browse")
        tree.heading("#0", text="Исходный термин")
        tree.heading("target", text="Целевой термин")
        tree.column("#0", width=320)
        tree.column("target", width=320)
        tree.grid(row=1, column=0, sticky="nsew")
        for term in store.glossary():
            tree.insert("", "end", text=term["source"], values=(term["target"],))
        bottom = ttk.Frame(self.content)
        bottom.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(bottom, text="Добавить термин", style="Accent.TButton", command=self._add_glossary_term).pack(side="left")
        ttk.Label(bottom, text="Совпадения без учёта регистра временно защищаются внутри каждого фрагмента.", style="Muted.TLabel", wraplength=600).pack(side="left", padx=12)

    def _add_glossary_term(self) -> None:
        store = self._active_store()
        if not store:
            return
        source = simpledialog.askstring("Термин глоссария", "Исходный термин:", parent=self.root)
        if source is None:
            return
        target = simpledialog.askstring("Термин глоссария", "Перевод:", parent=self.root)
        if target is None:
            return
        try:
            store.add_glossary_term(source, target)
            self.show_page("glossary")
        except ValueError as exc:
            messagebox.showerror("Пустой термин", str(exc), parent=self.root)

    def _show_settings(self) -> None:
        store = self._active_store()
        panel = self._panel(self.content)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        preferences = load_preferences(self.preferences_root)
        reduce_var = tk.BooleanVar(value=bool(preferences["reduce_motion"]))
        ttk.Label(panel, text="Параметры проекта и устройства", style="Panel.TLabel", font=(TOKENS["font"], 12, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 12))
        if store:
            model_var = tk.StringVar(value=store.project.get("model_id") or "qwen3-1.7b-q8")
            ttk.Label(panel, text="Модель проекта", style="Panel.TLabel").grid(row=1, column=0, sticky="w")
            model_combo = ttk.Combobox(panel, textvariable=model_var, state="readonly", values=[item["id"] for item in catalog()])
            model_combo.grid(row=2, column=0, sticky="ew", pady=(5, 10))
            ttk.Label(panel, text="Контекст проекта", style="Panel.TLabel").grid(row=3, column=0, sticky="w")
            context = self._text_widget(panel, height=3)
            context.grid(row=4, column=0, sticky="ew", pady=(5, 10))
            context.insert("1.0", store.project.get("context", ""))
            ttk.Label(panel, text="Правила перевода", style="Panel.TLabel").grid(row=5, column=0, sticky="w")
            rules = self._text_widget(panel, height=4)
            rules.grid(row=6, column=0, sticky="ew", pady=(5, 10))
            rules.insert("1.0", store.project.get("rules", ""))

            def save_project_settings() -> None:
                store.update_settings(
                    model_id=model_var.get(),
                    context=context.get("1.0", "end-1c"),
                    rules=rules.get("1.0", "end-1c"),
                )
                messagebox.showinfo("Сохранено", "Настройки проекта сохранены.", parent=self.root)

            ttk.Button(panel, text="Сохранить настройки проекта", style="Accent.TButton", command=save_project_settings).grid(row=7, column=0, sticky="e")
        hardware_label = self._hardware_text()
        self._settings_hardware = ttk.Label(panel, text=hardware_label, style="Muted.TLabel", justify="left", wraplength=720)
        self._settings_hardware.grid(row=8, column=0, sticky="w", pady=(18, 7))
        ttk.Button(panel, text="Проверить устройство", command=self._detect_hardware).grid(row=9, column=0, sticky="w")
        ttk.Checkbutton(panel, text="Уменьшить движение и переходы интерфейса", variable=reduce_var, command=lambda: self._save_motion(reduce_var.get())).grid(row=10, column=0, sticky="w", pady=(16, 0))
        ttk.Label(panel, text=f"Данные проекта: {self.projects_dir}\nВеса моделей: {self.models_dir}", style="Muted.TLabel", wraplength=700).grid(row=11, column=0, sticky="w", pady=(18, 0))

    def _save_motion(self, value: bool) -> None:
        preferences = load_preferences(self.preferences_root)
        preferences["reduce_motion"] = value
        save_preferences(preferences, self.preferences_root)
        self.reduce_motion = value

    def _hardware_text(self) -> str:
        if self.hardware is None:
            return "Устройство: не удалось определить (проверка не выполнена)."
        item = self.hardware
        ram = "не удалось определить" if item.ram_total_gb is None or item.ram_available_gb is None else f"{item.ram_total_gb:.1f} ГБ всего, {item.ram_available_gb:.1f} ГБ доступно"
        disk = "не удалось определить" if item.disk_free_gb is None else f"{item.disk_free_gb:.1f} ГБ свободно"
        if item.gpu_names is None:
            gpu = "не удалось определить"
        else:
            values = item.gpu_vram_gb or (None,) * len(item.gpu_names)
            gpu = "; ".join(f"{name} ({values[index]:.1f} ГБ VRAM)" if values[index] is not None else f"{name} (VRAM неизвестна)" for index, name in enumerate(item.gpu_names))
        runtime = "доступен" if item.llama_runtime_available else "не найден"
        gpu_runtime = "не определена" if item.llama_gpu_offload_available is None else ("поддерживается" if item.llama_gpu_offload_available else "не поддерживается")
        return f"Профиль: {item.profile}\nRAM: {ram}\nСвободный диск: {disk}\nGPU: {gpu}\nllama.cpp: {runtime}; GPU offload в этом backend: {gpu_runtime}."

    def _detect_hardware(self) -> None:
        self.sidebar_status.configure(text="Проверка устройства…")
        self._submit("hardware", lambda: detect(self.models_dir), self._hardware_done)

    def _hardware_done(self, result: Any) -> None:
        if isinstance(result, Exception):
            self.sidebar_status.configure(text=f"Не удалось определить устройство: {result}")
            return
        self.hardware = result
        self.sidebar_status.configure(text=f"Устройство: {result.profile}")
        if hasattr(self, "_settings_hardware") and self._settings_hardware.winfo_exists():
            self._settings_hardware.configure(text=self._hardware_text())
        if self.page == "models":
            self.show_page("models")

    def open_setup_wizard(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("Первый запуск · DotLingo")
        window.geometry("640x430")
        window.minsize(540, 380)
        window.transient(self.root)
        window.grab_set()
        window.configure(background=TOKENS["bg"])
        state: dict[str, Any] = {"step": 0, "selected": "qwen3-1.7b-q8", "cancel": None}
        panel = self._panel(window, padding=20)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(0, weight=1)
        ttk.Label(panel, text="Подготовка локального перевода", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        step_label = ttk.Label(panel, text="", style="Muted.TLabel")
        step_label.grid(row=1, column=0, sticky="w", pady=(0, 14))
        body = ttk.Frame(panel, style="Panel.TFrame")
        body.grid(row=2, column=0, sticky="nsew")
        panel.rowconfigure(2, weight=1)
        buttons = ttk.Frame(panel, style="Panel.TFrame")
        buttons.grid(row=3, column=0, sticky="ew", pady=(14, 0))

        def finish() -> None:
            prefs = load_preferences(self.preferences_root)
            prefs["setup_seen"] = True
            save_preferences(prefs, self.preferences_root)
            window.grab_release()
            window.destroy()
            self.show_page("projects")

        def render(step: int) -> None:
            state["step"] = step
            for widget in body.winfo_children():
                widget.destroy()
            for widget in buttons.winfo_children():
                widget.destroy()
            titles = ["1 · Проверка устройства", "2 · Рекомендация", "3 · Выбор и лицензия", "4 · Загрузка", "5 · Готово"]
            step_label.configure(text=titles[min(step, 4)])
            if step == 0:
                ttk.Label(body, text="Измеряем доступную RAM, свободное место, GPU и поддержку offload именно в llama.cpp.", style="Panel.TLabel", wraplength=550, justify="left").pack(anchor="w", pady=8)
                details = ttk.Label(body, text="Проверка выполняется локально. Если значение получить нельзя, оно останется неизвестным.", style="Muted.TLabel", wraplength=550, justify="left")
                details.pack(anchor="w", pady=8)
                threading.Thread(target=lambda: self.events.put(("wizard_hardware", detect(self.models_dir), details)), daemon=True).start()
                ttk.Button(buttons, text="Продолжить", style="Accent.TButton", command=lambda: render(1)).pack(side="right")
                ttk.Button(buttons, text="Отложить мастер", command=finish).pack(side="left")
            elif step == 1:
                selected = next((item for item in catalog() if item["id"] == state["selected"]), catalog()[0])
                estimate = f"Рассматриваемая модель: {selected['name']} · {_format_size(selected['size_bytes'])}. Это оценка требования к загрузке, а не обещание качества или скорости."
                ttk.Label(body, text=estimate, style="Panel.TLabel", wraplength=550, justify="left").pack(anchor="w", pady=8)
                ttk.Label(body, text=self._hardware_text(), style="Muted.TLabel", wraplength=550, justify="left").pack(anchor="w", pady=8)
                ttk.Label(body, text="Установка модели необязательна. Можно отложить её, проверить карточку и лицензию позже.", style="Muted.TLabel", wraplength=550, justify="left").pack(anchor="w", pady=8)
                ttk.Button(buttons, text="Назад", command=lambda: render(0)).pack(side="left")
                ttk.Button(buttons, text="Выбрать модель", style="Accent.TButton", command=lambda: render(2)).pack(side="right")
            elif step == 2:
                model_var = tk.StringVar(value=state["selected"])
                for item in catalog():
                    row = ttk.Frame(body, style="Panel.TFrame")
                    row.pack(fill="x", pady=3)
                    available = item.get("status") == "available"
                    ttk.Radiobutton(row, text=f"{item['name']} · {_format_size(item.get('size_bytes'))}", value=item["id"], variable=model_var, state="normal" if available else "disabled").pack(side="left")
                    ttk.Button(row, text="Карточка и лицензия", command=lambda url=item["license_url"]: webbrowser.open(url)).pack(side="right")
                consent_var = tk.BooleanVar(value=False)
                def selected_model() -> dict[str, Any]:
                    return next(item for item in catalog() if item["id"] == model_var.get())
                consent = ttk.Checkbutton(body, text="Я ознакомился с лицензией выбранной модели и соглашаюсь скачать её вес.", variable=consent_var, wraplength=520)
                consent.pack(anchor="w", pady=(12, 4))
                ttk.Label(body, text="Лицензии различаются. Размер, версия и SHA-256 показаны на странице «Модели». Неизвестная аппаратная совместимость останется помеченной.", style="Muted.TLabel", wraplength=550, justify="left").pack(anchor="w", pady=5)

                def start_download() -> None:
                    item = selected_model()
                    state["selected"] = item["id"]
                    if not consent_var.get():
                        messagebox.showwarning("Нужно согласие", "Прочитайте лицензию и отметьте согласие.", parent=window)
                        return
                    if not messagebox.askyesno("Скачать модель", f"Скачать {item['name']} ({_format_size(item['size_bytes'])}) по закреплённой ревизии?", parent=window):
                        return
                    state["cancel"] = threading.Event()
                    render(3)
                    cancel = state["cancel"]

                    def work() -> Path:
                        return download_model(item, self.models_dir, cancel=cancel, on_progress=lambda value: self.events.put(("wizard_progress", value)))

                    self._submit("wizard_download", work, lambda result: wizard_download_done(result))

                ttk.Button(buttons, text="Назад", command=lambda: render(1)).pack(side="left")
                ttk.Button(buttons, text="Отложить установку", command=lambda: render(4)).pack(side="right")
                ttk.Button(buttons, text="Скачать выбранную", style="Accent.TButton", command=start_download).pack(side="right", padx=8)
            elif step == 3:
                ttk.Label(body, text="Скачивание модели", style="Panel.TLabel", font=(TOKENS["font"], 12, "bold")).pack(anchor="w", pady=5)
                progress = ttk.Progressbar(body, mode="indeterminate")
                progress.pack(fill="x", pady=10)
                progress.start(12)
                status = ttk.Label(body, text="Ожидание ответа сервера · точный ETA не рассчитывается", style="Muted.TLabel", wraplength=550)
                status.pack(anchor="w")
                self._wizard_progress = (progress, status)
                ttk.Button(buttons, text="Отменить загрузку", command=lambda: state["cancel"].set() if state["cancel"] else None).pack(side="left")
            else:
                ttk.Label(body, text="DotLingo готов. Исходники и проекты сохраняются отдельно от весов.", style="Panel.TLabel", wraplength=550, justify="left").pack(anchor="w", pady=8)
                runtime_note = "Runtime llama.cpp найден." if self._runtime_available() else "Runtime llama.cpp не найден в текущем окружении. Перевод будет доступен в сборке, где он установлен; один вес модели для запуска недостаточен."
                ttk.Label(body, text=runtime_note, style="Muted.TLabel", wraplength=550, justify="left").pack(anchor="w", pady=8)
                ttk.Label(body, text="Распознавание сканированных PDF через OCR не устанавливается автоматически.", style="Muted.TLabel", wraplength=550, justify="left").pack(anchor="w", pady=8)
                ttk.Button(buttons, text="Готово", style="Accent.TButton", command=finish).pack(side="right")

        def wizard_download_done(result: Any) -> None:
            if isinstance(result, Exception):
                messagebox.showwarning("Загрузка не завершена", str(result), parent=window)
                render(2)
            else:
                render(4)

        render(0)
        window.bind("<Escape>", lambda _: finish())

    def _need_project(self) -> None:
        ttk.Label(self.content, text="Сначала создайте или выберите проект.", style="Section.TLabel").grid(row=0, column=0, sticky="nw", pady=14)
        ttk.Button(self.content, text="Перейти к проектам", command=lambda: self.show_page("projects")).grid(row=1, column=0, sticky="nw")

    def _queue_for(self, project: ProjectStore) -> TaskQueue:
        key = str(project.root)
        if key not in self.project_queues:
            self.project_queues[key] = TaskQueue(project, self.models_dir, on_event=lambda event, project_id=project.project["id"]: self.events.put(("task", project_id, event)))
        return self.project_queues[key]

    @staticmethod
    def _runtime_available() -> bool:
        try:
            import llama_cpp  # noqa: F401
        except ImportError:
            return False
        return True

    def _submit(self, name: str, work: Callable[[], Any], done: Callable[[Any], None]) -> None:
        self._set_busy(True, f"{name}…")

        def run() -> None:
            try:
                result: Any = work()
            except Exception as exc:
                result = exc
            self.events.put(("job_done", name, result, done))

        threading.Thread(target=run, name=f"DotLingo {name}", daemon=True).start()

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.sidebar_status.configure(text=message if busy else "Локальная работа · без облака")

    def _poll_events(self) -> None:
        if self._closing:
            return
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "job_done":
                    _, _, result, callback = event
                    self._set_busy(False)
                    callback(result)
                elif kind == "task":
                    _, project_id, task_event = event
                    self.sidebar_status.configure(text=f"{self._status_label(task_event['status'])}: {task_event.get('completed', 0)}/{task_event.get('total', 0)}")
                    if self.page == "queue":
                        self.show_page("queue")
                    elif self.page == "documents" and task_event["status"] in {"complete", "failed", "cancelled", "interrupted"}:
                        self.show_page("documents")
                elif kind == "download_progress":
                    self._update_download_progress(event[1])
                elif kind == "wizard_progress":
                    self._update_wizard_progress(event[1])
                elif kind == "wizard_hardware":
                    _, result, widget = event
                    self.hardware = result
                    try:
                        widget.configure(text=self._hardware_text())
                    except tk.TclError:
                        pass
        except queue.Empty:
            pass
        self.root.after(150, self._poll_events)

    def _update_download_progress(self, value: dict[str, Any]) -> None:
        if not self._download_window or not self._download_window.winfo_exists() or not hasattr(self, "_download_progress_widgets"):
            return
        label, progress = self._download_progress_widgets
        amount = int(value.get("bytes", 0))
        total = int(value.get("total", 0))
        progress.configure(value=(amount * 100 / total) if total else 0)
        label.configure(text=f"Получено {_format_size(amount)} из {_format_size(total)} · ETA не показывается")

    def _update_wizard_progress(self, value: dict[str, Any]) -> None:
        if not hasattr(self, "_wizard_progress"):
            return
        progress, label = self._wizard_progress
        try:
            amount = int(value.get("bytes", 0))
            total = int(value.get("total", 0))
            progress.stop()
            progress.configure(mode="determinate", maximum=100, value=(amount * 100 / total) if total else 0)
            label.configure(text=f"Получено {_format_size(amount)} из {_format_size(total)} · ETA не показывается")
        except tk.TclError:
            pass

    def _smoke_check(self) -> None:
        required = {"projects", "documents", "queue", "review", "models", "glossary", "settings"}
        if set(self.nav_buttons) != required:
            self.root.destroy()
            raise RuntimeError("UI smoke check failed: navigation incomplete")
        self.show_page("projects")
        self.root.update_idletasks()
        self.root.destroy()

    def close(self) -> None:
        if self._closing:
            return
        if not self._confirm_review_change():
            return
        self._closing = True
        if self._download_cancel:
            self._download_cancel.set()
        for task_queue in self.project_queues.values():
            task_queue.close(timeout=1)
        self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="Локальный перевод документов и книг")
    parser.add_argument("--data-dir", type=Path, default=None, help="путь пользовательских данных (для сборки и проверок)")
    parser.add_argument("--smoke-test", action="store_true", help="создать и закрыть UI без моделей")
    args = parser.parse_args()
    root = tk.Tk()
    DotLingoApp(root, args.data_dir, smoke=args.smoke_test)
    if args.smoke_test:
        root.mainloop()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
