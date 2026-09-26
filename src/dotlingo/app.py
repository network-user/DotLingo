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
from dotlingo.model_download import DownloadCancellation, DownloadCancelled, download_model
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
        self._review_block_orders: set[int] = set()
        self._review_progress_label: ttk.Label | None = None
        self._review_loading = False
        self._review_refilter: Callable[[], None] | None = None
        self._model_checks: dict[str, tk.BooleanVar] = {}
        self._download_cancel: DownloadCancellation | None = None
        self._download_worker: threading.Thread | None = None
        self._download_window: tk.Frame | None = None
        self._overlay: tk.Frame | None = None
        self._overlay_previous_focus: tk.Widget | None = None
        self._overlay_escape: Callable[[tk.Event[Any]], str | None] | None = None
        self._page_animation_job: str | None = None
        self._settings_form: tuple[ProjectStore, tk.StringVar, tk.Text, tk.Text, tuple[str, str, str]] | None = None
        self._closing = False
        self._pending_close = False
        self._close_wait_job: str | None = None
        self._close_cancel_accepted: bool | None = None

        self.root.title("DotLingo · локальный перевод документов")
        self.root.geometry("1160x760")
        self.root.minsize(760, 540)
        self._set_icon()
        self._style()
        self._build_shell()
        self._refresh_project_list()
        self._restore_active_project()
        for index, (page, _) in enumerate(PAGES, start=1):
            self.root.bind(
                f"<Control-Key-{index}>",
                lambda _event, selected_page=page: self._navigate_shortcut(selected_page),
            )
        self.root.bind("<Control-f>", lambda _event: self._find_shortcut())
        self.root.bind("<Escape>", self._handle_overlay_escape)
        self.root.bind("<Configure>", self._on_root_configure, add="+")
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
        style.configure("Error.TLabel", background=colors["surface"], foreground=colors["error"], font=(colors["font"], colors["font_small"]))
        style.configure("Panel.TLabel", background=colors["surface"], foreground=colors["text"], font=(colors["font"], colors["font_body"]))
        style.configure("Title.TLabel", background=colors["bg"], foreground=colors["text"], font=(colors["font"], colors["font_title"], "bold"))
        style.configure("Section.TLabel", background=colors["bg"], foreground=colors["text"], font=(colors["font"], colors["font_heading"], "bold"))
        style.configure("TButton", background=colors["surface_raised"], foreground=colors["text"], padding=(10, 7), bordercolor=colors["border"])
        style.map("TButton", background=[("active", colors["surface_hover"]), ("pressed", colors["accent_dim"])], foreground=[("disabled", colors["muted"])])
        style.configure("Accent.TButton", background=colors["accent_dim"], foreground=colors["text"], padding=(11, 8), bordercolor=colors["accent"])
        style.map("Accent.TButton", background=[("active", colors["accent"]), ("pressed", colors["accent_dim"])])
        style.configure("Nav.TButton", background=colors["bg"], foreground=colors["muted"], anchor="w", padding=(12, 9), borderwidth=0)
        style.map("Nav.TButton", background=[("active", colors["surface_hover"]), ("pressed", colors["accent_dim"])], foreground=[("active", colors["text"])])
        style.configure("ActiveNav.TButton", background=colors["surface_raised"], foreground=colors["accent"], anchor="w", padding=(12, 9), borderwidth=0)
        style.map("ActiveNav.TButton", background=[("active", colors["surface_hover"]), ("pressed", colors["accent_dim"])], foreground=[("active", colors["text"])])
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
        self.page_host = ttk.Frame(workspace)
        self.page_host.grid(row=1, column=0, sticky="nsew")
        self.page_host.rowconfigure(0, weight=1)
        self.page_host.columnconfigure(0, weight=1)
        self.content = ttk.Frame(self.page_host)
        self.content.grid(row=0, column=0, sticky="nsew")
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
        search_job = getattr(self, "_review_search_job", None)
        if search_job is not None:
            try:
                self.root.after_cancel(search_job)
            except tk.TclError:
                pass
            self._review_search_job = None
        resize_job = getattr(self, "_review_resize_job", None)
        if resize_job is not None:
            try:
                self.root.after_cancel(resize_job)
            except tk.TclError:
                pass
            self._review_resize_job = None
        self._review_body = None
        self._review_progress_label = None
        self._review_refilter = None
        for child in self.content.winfo_children():
            child.destroy()

    def show_page(self, page: str) -> None:
        if page not in dict(PAGES):
            return
        if self.page == "review" and not self._confirm_review_change():
            return
        if self.page == "settings" and not self._confirm_settings_change():
            return
        self._stop_page_animation()
        self.page = page
        labels = dict(PAGES)
        self.header_title.configure(text=labels[page])
        self.header_project.configure(text=self.active.project["title"] if self.active else "Проект не выбран")
        for key, button in self.nav_buttons.items():
            button.configure(style="ActiveNav.TButton" if key == page else "Nav.TButton")
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
        self._animate_page_change()

    def _navigate_shortcut(self, page: str) -> str:
        if self._overlay is not None and self._overlay.winfo_exists():
            return "break"
        self.show_page(page)
        return "break"

    def _find_shortcut(self) -> str:
        if self._overlay is None or not self._overlay.winfo_exists():
            self._focus_review_search()
        return "break"

    def _animate_page_change(self) -> None:
        if self.reduce_motion or self._closing or not self.content.winfo_exists():
            return
        self.content.grid_remove()
        self.content.place(relx=0, rely=0, relwidth=1, relheight=1, x=12)
        steps = 5

        def advance(step: int) -> None:
            if self._closing or not self.content.winfo_exists():
                self._page_animation_job = None
                return
            offset = round(12 * (1 - step / steps))
            self.content.place_configure(x=offset)
            if step >= steps:
                self.content.place_forget()
                self.content.grid(row=0, column=0, sticky="nsew")
                self._page_animation_job = None
                return
            self._page_animation_job = self.root.after(16, lambda: advance(step + 1))

        self._page_animation_job = self.root.after(16, lambda: advance(1))

    def _stop_page_animation(self) -> None:
        if self._page_animation_job is None:
            return
        try:
            self.root.after_cancel(self._page_animation_job)
        except tk.TclError:
            pass
        self._page_animation_job = None
        if self.content.winfo_exists():
            self.content.place_forget()
            self.content.grid(row=0, column=0, sticky="nsew")

    def _focus_review_search(self) -> None:
        if self.page != "review":
            self.show_page("review")
        search = getattr(self, "_review_search", None)
        if search is not None and search.winfo_exists():
            search.focus_set()

    def _open_overlay(self) -> tuple[tk.Frame, ttk.Frame]:
        if self._overlay is not None and self._overlay.winfo_exists():
            self._close_overlay(self._overlay)
        self._overlay_previous_focus = self.root.focus_get()
        overlay = tk.Frame(self.root, background=TOKENS["bg"], takefocus=True)
        overlay.place(x=0, y=0, relwidth=1, relheight=1)
        overlay.lift()
        overlay.grab_set()
        panel = self._panel(overlay, padding=20)
        panel.place(relx=0.5, rely=0.5, anchor="center", width=760, height=560)

        def fit_panel(width: int, height: int) -> None:
            panel.place_configure(width=min(760, max(520, width - 40)), height=min(560, max(360, height - 40)))

        overlay.bind("<Configure>", lambda event: fit_panel(event.width, event.height), add="+")
        fit_panel(self.root.winfo_width(), self.root.winfo_height())
        self._overlay = overlay
        return overlay, panel

    def _handle_overlay_escape(self, event: tk.Event[Any]) -> str | None:
        if self._overlay_escape is not None:
            return self._overlay_escape(event)
        return None

    def _close_overlay(self, overlay: tk.Frame) -> None:
        if not overlay.winfo_exists():
            return
        try:
            if self.root.grab_current() == overlay:
                overlay.grab_release()
        except tk.TclError:
            pass
        overlay.destroy()
        self._overlay_escape = None
        if self._overlay == overlay:
            self._overlay = None
        previous = self._overlay_previous_focus
        self._overlay_previous_focus = None
        try:
            if previous is not None and previous.winfo_exists():
                previous.focus_set()
            else:
                self.root.focus_set()
        except tk.TclError:
            pass

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
        for project in self.projects:
            for task in project.tasks():
                task_id = task["id"]
                tree.insert("", "end", iid=task_id, values=self._queue_values(project, task), tags=(task["status"],))
        tree.tag_configure("complete", foreground=TOKENS["accent"])
        tree.tag_configure("failed", foreground=TOKENS["error"])
        button_row = ttk.Frame(panel, style="Panel.TFrame")
        button_row.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(button_row, text="Продолжить / повторить", command=lambda: self._queue_action(tree, "resume")).pack(side="left")
        ttk.Button(button_row, text="Пауза", command=lambda: self._queue_action(tree, "pause")).pack(side="left", padx=7)
        ttk.Button(button_row, text="Отменить", command=lambda: self._queue_action(tree, "cancel")).pack(side="left")
        ttk.Button(button_row, text="Обновить", command=lambda: self.show_page("queue")).pack(side="right")
        self._queue_tree = tree

    def _queue_values(self, project: ProjectStore, task: dict[str, Any]) -> tuple[str, str, str, str, str]:
        document = next((item for item in project.documents() if item.id == task["document_id"]), None)
        model_title = next(
            (item["name"] for item in catalog() if item["id"] == task["model_id"]),
            task["model_id"],
        )
        return (
            document.name if document else "Документ",
            self._status_label(task["status"]),
            f"{task['completed']}/{task['total']}",
            model_title,
            task["error"] or "",
        )

    def _update_queue_row(self, project_id: str, task_event: dict[str, Any]) -> None:
        tree = getattr(self, "_queue_tree", None)
        if self.page != "queue" or tree is None or not tree.winfo_exists():
            return
        project = next((item for item in self.projects if item.project["id"] == project_id), None)
        if project is None:
            return
        task_id = task_event["task_id"]
        try:
            task = project.task(task_id)
        except KeyError:
            return
        if tree.exists(task_id):
            tree.item(task_id, values=self._queue_values(project, task), tags=(task["status"],))
        else:
            tree.insert("", "end", iid=task_id, values=self._queue_values(project, task), tags=(task["status"],))

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
        self._review_progress_label = ttk.Label(toolbar, text=f"Переведено блоков: {complete}/{len(blocks)}", style="Muted.TLabel")
        self._review_progress_label.grid(row=0, column=1, sticky="e", padx=10)
        ttk.Button(toolbar, text="Сохранить правку", command=self._save_review_edit).grid(row=0, column=2, padx=4)
        ttk.Button(toolbar, text="Экспорт…", style="Accent.TButton", command=self._export_current).grid(row=0, column=3)

        body = ttk.Panedwindow(self.content, orient="horizontal")
        body.grid(row=1, column=0, sticky="nsew")
        navigation = self._panel(body, padding=8)
        navigation.columnconfigure(0, weight=1)
        navigation.rowconfigure(2, weight=1)
        ttk.Label(navigation, text="Поиск по документу", style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 5))
        search = ttk.Entry(navigation)
        search.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        tree = ttk.Treeview(navigation, show="tree", selectmode="browse")
        tree.grid(row=2, column=0, sticky="nsew")
        tree_scroll = ttk.Scrollbar(navigation, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.grid(row=2, column=1, sticky="ns")
        translation_area = ttk.Frame(body)
        translation_area.columnconfigure(0, weight=1)
        translation_area.columnconfigure(2, weight=1)
        translation_area.rowconfigure(1, weight=1)
        ttk.Label(translation_area, text="Оригинал", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6), padx=(0, 8))
        ttk.Label(translation_area, text="Перевод · можно редактировать", style="Section.TLabel").grid(row=0, column=2, columnspan=2, sticky="w", pady=(0, 6), padx=(8, 0))
        source_text = self._text_widget(translation_area, height=15, disabled=True)
        target_text = self._text_widget(translation_area, height=15)
        source_scroll = ttk.Scrollbar(translation_area, orient="vertical", command=source_text.yview)
        target_scroll = ttk.Scrollbar(translation_area, orient="vertical", command=target_text.yview)
        source_text.configure(yscrollcommand=source_scroll.set)
        target_text.configure(yscrollcommand=target_scroll.set)
        source_text.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
        source_scroll.grid(row=1, column=1, sticky="ns")
        target_text.grid(row=1, column=2, sticky="nsew", padx=(5, 0))
        target_scroll.grid(row=1, column=3, sticky="ns")
        self._review_target = target_text
        self._review_source = source_text
        self._review_tree = tree
        self._review_search = search
        self._review_block = None
        self._review_block_orders = {block.order for block in blocks}
        search_state: dict[str, Any] = {"job": None, "query": ""}

        groups: dict[str, list[Any]] = {}
        for block in blocks:
            groups.setdefault(block.section, []).append(block)

        def fill_tree(query: str = "") -> None:
            nonlocal translated
            lowered = query.casefold().strip()
            translated = store.translations(document.id)

            def matching_blocks() -> list[Any]:
                if not lowered:
                    return blocks
                return [block for block in blocks if lowered in block.text.casefold() or lowered in translated.get(block.order, "").casefold()]

            matches = matching_blocks()
            visible_orders = {block.order for block in matches}
            previous_order = self._review_block
            if previous_order is not None and previous_order not in visible_orders and self._review_target.edit_modified():
                old = self._review_target.get("1.0", "end-1c")
                if old != self._review_text_original:
                    decision = messagebox.askyesnocancel(
                        "Несохранённая правка",
                        "Сохранить изменения перевода перед фильтрацией текущего блока?",
                        parent=self.root,
                    )
                    if decision is None:
                        search.delete(0, "end")
                        search.insert(0, search_state["query"])
                        return
                    if decision:
                        self._save_review_edit()
                        translated = store.translations(document.id)
                        matches = matching_blocks()
                        visible_orders = {block.order for block in matches}
                    else:
                        self._review_target.edit_modified(False)
            tree.delete(*tree.get_children())
            self._review_order = []
            if lowered:
                parent = tree.insert("", "end", text="Результаты поиска" if matches else "Совпадений нет", open=True)
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
            selected = f"b{previous_order}" if previous_order is not None and previous_order in visible_orders else ""
            if not selected:
                first = next((item for item in tree.get_children("") if tree.get_children(item)), None)
                if first:
                    selected = tree.get_children(first)[0]
            if selected:
                child = selected
                tree.selection_set(child)
                tree.focus(child)
                select_block(child)
            search_state["query"] = query

        def select_block(iid: str) -> None:
            if not iid.startswith("b"):
                return
            try:
                order = int(iid[1:])
            except ValueError:
                return
            if order == self._review_block:
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
        def schedule_search(_event: tk.Event[Any] | None = None) -> None:
            job = search_state["job"]
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
            def run_search() -> None:
                search_state["job"] = None
                self._review_search_job = None
                if search.winfo_exists():
                    fill_tree(search.get())

            search_state["job"] = self.root.after(180, run_search)
            self._review_search_job = search_state["job"]

        search.bind("<KeyRelease>", schedule_search)
        self._review_refilter = lambda: fill_tree(search.get()) if search.winfo_exists() else None
        body.add(navigation, weight=1)
        body.add(translation_area, weight=4)
        self._review_body = body
        self._adapt_review_layout()
        fill_tree()

    def _on_root_configure(self, event: tk.Event[Any]) -> None:
        if event.widget is not self.root or self.page != "review":
            return
        job = getattr(self, "_review_resize_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self._review_resize_job = self.root.after(80, self._adapt_review_layout)

    def _adapt_review_layout(self) -> None:
        body = getattr(self, "_review_body", None)
        if body is None or not body.winfo_exists():
            return
        if getattr(self, "_review_resize_job", None) is not None:
            self._review_resize_job = None
        orient = "vertical" if self.root.winfo_width() < 980 else "horizontal"
        if body.cget("orient") != orient:
            body.configure(orient=orient)
            def position_sash() -> None:
                if not body.winfo_exists():
                    return
                try:
                    body.sashpos(0, 165 if orient == "vertical" else max(190, int(body.winfo_width() * 0.22)))
                except tk.TclError:
                    pass

            body.after_idle(position_sash)

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
        translations = store.translations(self._selected_doc)
        if self.page == "review" and self._review_refilter is not None:
            try:
                self.root.after_idle(self._review_refilter)
            except tk.TclError:
                pass
        if self._review_progress_label is not None and self._review_progress_label.winfo_exists():
            complete = sum(1 for order in self._review_block_orders if order in translations)
            self._review_progress_label.configure(text=f"Переведено блоков: {complete}/{len(self._review_block_orders)}")
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
        cancel = DownloadCancellation()
        self._download_cancel = cancel
        window, panel = self._open_overlay()
        def cancel_download(_event: tk.Event[Any] | None = None) -> str:
            accepted = cancel.set()
            if label.winfo_exists():
                message = "Отмена запрошена · временный файл не будет активирован" if accepted else "Проверка завершилась · дождитесь активации проверенного файла"
                label.configure(text=message)
            return "break"

        self._overlay_escape = cancel_download
        header = ttk.Frame(panel, style="Panel.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text=f"Загрузка · {model['name']}", style="Section.TLabel").pack(side="left", anchor="w")
        close_button = ttk.Button(header, text="×", width=3, command=cancel_download)
        close_button.pack(side="right")
        ttk.Label(panel, text="Файл проверяется по размеру и SHA-256 перед активацией.", style="Muted.TLabel").pack(anchor="w", pady=(12, 4))
        label = ttk.Label(
            panel,
            text=f"Получено 0 из {_format_size(model['size_bytes'])} · ETA не показывается",
            style="Panel.TLabel",
            wraplength=680,
            justify="left",
        )
        label.pack(anchor="w", pady=9)
        progress = ttk.Progressbar(panel, mode="determinate", maximum=100)
        progress.pack(fill="x", pady=(2, 10))
        action = ttk.Button(panel, text="Отменить и продолжить позже", command=cancel_download)
        action.pack(anchor="e")
        self._download_window = window

        def progress_event(value: dict[str, Any]) -> None:
            self.events.put(("download_progress", value))

        def work() -> Path:
            return download_model(model, self.models_dir, cancel=cancel, on_progress=progress_event)

        self._download_worker = self._submit("download", work, lambda result: self._download_done(model, result))
        self._download_progress_widgets = (label, progress)
        self._download_action = action
        self._download_close_button = close_button

    def _download_done(self, model: dict[str, Any], result: Any) -> None:
        self._download_cancel = None
        if self._pending_close:
            return
        if not self._download_window or not self._download_window.winfo_exists():
            return
        label, progress = self._download_progress_widgets
        if isinstance(result, Exception):
            title = "Загрузка отменена" if isinstance(result, DownloadCancelled) else "Ошибка загрузки"
            progress.stop()
            label.configure(text=f"{title} · {model['name']}: {result}")
            self._overlay_escape = lambda _event: self._dismiss_download_overlay() or "break"
        else:
            progress.stop()
            progress.configure(mode="determinate", maximum=100, value=100)
            label.configure(text=f"Модель «{model['name']}» загружена и проверена. Файл активирован: {result}")
            self._overlay_escape = lambda _event: self._dismiss_download_overlay() or "break"
        self._download_action.configure(text="Закрыть", command=self._dismiss_download_overlay)
        self._download_close_button.configure(text="×", command=self._dismiss_download_overlay)
        self.show_page("models")

    def _dismiss_download_overlay(self) -> str:
        if self._download_window is not None and self._download_window.winfo_exists():
            self._close_overlay(self._download_window)
        self._download_window = None
        self._download_cancel = None
        self._download_progress_widgets = None
        self._download_action = None
        self._download_close_button = None
        return "break"

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
        self._settings_form = None
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
            initial = (model_var.get(), context.get("1.0", "end-1c"), rules.get("1.0", "end-1c"))
            self._settings_form = (store, model_var, context, rules, initial)

            def save_project_settings() -> None:
                self._persist_settings_form()

            ttk.Button(panel, text="Сохранить настройки проекта", style="Accent.TButton", command=save_project_settings).grid(row=7, column=0, sticky="e")
        hardware_label = self._hardware_text()
        self._settings_hardware = ttk.Label(panel, text=hardware_label, style="Muted.TLabel", justify="left", wraplength=720)
        self._settings_hardware.grid(row=8, column=0, sticky="w", pady=(18, 7))
        ttk.Button(panel, text="Проверить устройство", command=self._detect_hardware).grid(row=9, column=0, sticky="w")
        ttk.Checkbutton(panel, text="Уменьшить движение и переходы интерфейса", variable=reduce_var, command=lambda: self._save_motion(reduce_var.get())).grid(row=10, column=0, sticky="w", pady=(16, 0))
        ttk.Label(panel, text=f"Данные проекта: {self.projects_dir}\nВеса моделей: {self.models_dir}", style="Muted.TLabel", wraplength=700).grid(row=11, column=0, sticky="w", pady=(18, 0))

    def _settings_form_values(self) -> tuple[str, str, str] | None:
        form = self._settings_form
        if form is None:
            return None
        _, model_var, context, rules, _ = form
        return model_var.get(), context.get("1.0", "end-1c"), rules.get("1.0", "end-1c")

    def _persist_settings_form(self) -> bool:
        form = self._settings_form
        values = self._settings_form_values()
        if form is None or values is None:
            return True
        store = form[0]
        try:
            store.update_settings(model_id=values[0], context=values[1], rules=values[2])
        except Exception as exc:
            self.sidebar_status.configure(text=f"Не удалось сохранить настройки: {exc}")
            return False
        self._settings_form = (store, form[1], form[2], form[3], values)
        self.sidebar_status.configure(text="Настройки проекта сохранены")
        return True

    def _confirm_settings_change(self) -> bool:
        form = self._settings_form
        values = self._settings_form_values()
        if form is None or values is None or values == form[4]:
            return True
        decision = messagebox.askyesnocancel(
            "Несохранённые настройки",
            "Сохранить изменения настроек проекта перед переходом?",
            parent=self.root,
        )
        if decision is None:
            return False
        if decision:
            return self._persist_settings_form()
        self._settings_form = None
        return True

    def _save_motion(self, value: bool) -> None:
        preferences = load_preferences(self.preferences_root)
        preferences["reduce_motion"] = value
        try:
            save_preferences(preferences, self.preferences_root)
        except OSError as exc:
            self.sidebar_status.configure(text=f"Не удалось сохранить настройку: {exc}")
            return
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
        if self._overlay is not None and self._overlay.winfo_exists():
            return
        window, panel = self._open_overlay()
        state: dict[str, Any] = {
            "step": 0,
            "selected": "qwen3-1.7b-q8",
            "cancel": None,
            "hardware_started": False,
            "closed": False,
        }
        panel.columnconfigure(0, weight=1)
        header = ttk.Frame(panel, style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Подготовка локального перевода", style="Section.TLabel").pack(side="left", anchor="w")
        ttk.Button(header, text="Отложить", command=lambda: finish()).pack(side="right")
        step_label = ttk.Label(panel, text="", style="Muted.TLabel")
        step_label.grid(row=1, column=0, sticky="w", pady=(14, 6))
        step_progress = ttk.Progressbar(panel, mode="determinate", maximum=5)
        step_progress.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        body = ttk.Frame(panel, style="Panel.TFrame")
        body.grid(row=3, column=0, sticky="nsew")
        panel.rowconfigure(3, weight=1)
        notice = ttk.Label(panel, text="", style="Error.TLabel", wraplength=680, justify="left")
        notice.grid(row=4, column=0, sticky="w", pady=(8, 0))
        buttons = ttk.Frame(panel, style="Panel.TFrame")
        buttons.grid(row=5, column=0, sticky="ew", pady=(12, 0))

        def finish() -> None:
            if state["closed"]:
                return
            if state["step"] == 3:
                notice.configure(text="Сначала дождитесь завершения загрузки или её отмены.")
                return
            prefs = load_preferences(self.preferences_root)
            prefs["setup_seen"] = True
            try:
                save_preferences(prefs, self.preferences_root)
            except OSError as exc:
                notice.configure(text=f"Не удалось сохранить настройку первого запуска: {exc}")
                return
            state["closed"] = True
            if hasattr(self, "_wizard_progress"):
                del self._wizard_progress
            self._close_overlay(window)
            self.show_page("projects")

        def hardware_done(result: Any, details: ttk.Label, continue_button: ttk.Button) -> None:
            if isinstance(result, Exception):
                self.hardware = None
                details_text = f"Не удалось определить устройство. Можно продолжить с неизвестными параметрами.\n{result}"
            else:
                self.hardware = result
                details_text = self._hardware_text()
            try:
                if state["closed"] or not window.winfo_exists():
                    return
                details.configure(text=details_text)
                if continue_button.winfo_exists():
                    continue_button.configure(state="normal")
                    continue_button.focus_set()
            except tk.TclError:
                pass

        def render(step: int) -> None:
            state["step"] = step
            notice.configure(text="")
            for widget in body.winfo_children():
                widget.destroy()
            for widget in buttons.winfo_children():
                widget.destroy()
            titles = ["1 · Проверка устройства", "2 · Рекомендация", "3 · Выбор и лицензия", "4 · Загрузка", "5 · Готово"]
            step_label.configure(text=titles[min(step, 4)])
            step_progress.configure(value=step + 1)
            focus_target: ttk.Widget | None = None
            if step == 0:
                ttk.Label(body, text="Проверяем доступную RAM, свободное место, GPU и поддержку offload именно в llama.cpp.", style="Panel.TLabel", wraplength=680, justify="left").pack(anchor="w", pady=8)
                details = ttk.Label(body, text="Проверка выполняется локально. Если значение получить нельзя, оно останется неизвестным.", style="Muted.TLabel", wraplength=680, justify="left")
                details.pack(anchor="w", pady=8)
                continue_button = ttk.Button(buttons, text="Продолжить", style="Accent.TButton", command=lambda: render(1), state="disabled")
                continue_button.pack(side="right")
                ttk.Button(buttons, text="Отложить мастер", command=finish).pack(side="left")
                focus_target = continue_button
                if not state["hardware_started"]:
                    state["hardware_started"] = True
                    self._submit(
                        "device_check",
                        lambda: detect(self.models_dir),
                        lambda result: hardware_done(result, details, continue_button),
                    )
                else:
                    continue_button.configure(state="normal")
            elif step == 1:
                selected = next((item for item in catalog() if item["id"] == state["selected"]), catalog()[0])
                estimate = f"Вариант для оценки: {selected['name']} · {_format_size(selected['size_bytes'])}. Размер загрузки указан по карточке модели."
                ttk.Label(body, text=estimate, style="Panel.TLabel", wraplength=680, justify="left").pack(anchor="w", pady=8)
                ttk.Label(body, text=self._hardware_text(), style="Muted.TLabel", wraplength=680, justify="left").pack(anchor="w", pady=8)
                ttk.Label(body, text="Установка модели необязательна. Можно отложить её и выбрать модель позже в разделе «Модели».", style="Muted.TLabel", wraplength=680, justify="left").pack(anchor="w", pady=8)
                ttk.Button(buttons, text="Назад", command=lambda: render(0)).pack(side="left")
                focus_target = ttk.Button(buttons, text="Выбрать модель", style="Accent.TButton", command=lambda: render(2))
                focus_target.pack(side="right")
            elif step == 2:
                model_var = tk.StringVar(value=state["selected"])
                for item in catalog():
                    row = ttk.Frame(body, style="Panel.TFrame")
                    row.pack(fill="x", pady=3)
                    available = item.get("status") == "available"
                    ttk.Radiobutton(
                        row,
                        text=f"{item['name']} · {_format_size(item.get('size_bytes'))}",
                        value=item["id"],
                        variable=model_var,
                        state="normal" if available else "disabled",
                    ).pack(side="left")
                    ttk.Button(row, text="Карточка и лицензия", command=lambda url=item["license_url"]: webbrowser.open(url)).pack(side="right")
                model_var.trace_add("write", lambda *_args: state.update(selected=model_var.get()))
                consent_var = tk.BooleanVar(value=False)
                consent = ttk.Checkbutton(body, text="Я ознакомился с лицензией выбранной модели и соглашаюсь скачать её вес.", variable=consent_var, wraplength=680)
                consent.pack(anchor="w", pady=(12, 4))
                ttk.Label(body, text="Лицензии различаются. Размер, версия и SHA-256 закреплены в карточке модели. Неизвестная совместимость не считается подтверждённой.", style="Muted.TLabel", wraplength=680, justify="left").pack(anchor="w", pady=5)

                def start_download() -> None:
                    item = next(entry for entry in catalog() if entry["id"] == model_var.get())
                    state["selected"] = item["id"]
                    if not consent_var.get():
                        notice.configure(text="Отметьте согласие с лицензией перед загрузкой.")
                        return
                    state["cancel"] = DownloadCancellation()
                    self._download_cancel = state["cancel"]
                    render(3)
                    cancel = state["cancel"]

                    def work() -> Path:
                        return download_model(
                            item,
                            self.models_dir,
                            cancel=cancel,
                            on_progress=lambda value: self.events.put(("wizard_progress", value)),
                        )

                    self._download_worker = self._submit("wizard_download", work, wizard_download_done)

                ttk.Button(buttons, text="Назад", command=lambda: render(1)).pack(side="left")
                ttk.Button(buttons, text="Отложить установку", command=lambda: render(4)).pack(side="right")
                focus_target = ttk.Button(buttons, text="Скачать выбранную", style="Accent.TButton", command=start_download)
                focus_target.pack(side="right", padx=8)
            elif step == 3:
                ttk.Label(body, text="Скачивание модели", style="Panel.TLabel", font=(TOKENS["font"], 12, "bold")).pack(anchor="w", pady=5)
                progress = ttk.Progressbar(body, mode="indeterminate")
                progress.pack(fill="x", pady=10)
                progress.start(12)
                status = ttk.Label(body, text="Ожидание ответа сервера · общий прогресс и ETA могут быть неизвестны", style="Muted.TLabel", wraplength=680)
                status.pack(anchor="w")
                self._wizard_progress = (progress, status)

                def cancel_download() -> None:
                    accepted = False
                    if state["cancel"] is not None:
                        accepted = state["cancel"].set()
                    if accepted:
                        notice.configure(text="Отмена запрошена. Временный файл не будет активирован.")
                    else:
                        notice.configure(text="Проверка завершилась. Дождитесь активации проверенного файла.")
                state["cancel_action"] = cancel_download

                focus_target = ttk.Button(buttons, text="Отменить загрузку", command=cancel_download)
                focus_target.pack(side="left")
            else:
                ttk.Label(body, text="DotLingo готов. Исходники, проекты и веса хранятся отдельно.", style="Panel.TLabel", wraplength=680, justify="left").pack(anchor="w", pady=8)
                runtime_note = "Runtime llama.cpp найден." if self._runtime_available() else "Runtime llama.cpp не найден в текущем окружении. Для перевода нужен установленный runtime и проверенная модель."
                ttk.Label(body, text=runtime_note, style="Muted.TLabel", wraplength=680, justify="left").pack(anchor="w", pady=8)
                ttk.Label(body, text="OCR для сканированных PDF не устанавливается автоматически.", style="Muted.TLabel", wraplength=680, justify="left").pack(anchor="w", pady=8)
                focus_target = ttk.Button(buttons, text="Готово", style="Accent.TButton", command=finish)
                focus_target.pack(side="right")
            if focus_target is not None:
                focus_target.focus_set()

        def wizard_download_done(result: Any) -> None:
            state["cancel"] = None
            self._download_cancel = None
            if self._pending_close:
                return
            if isinstance(result, Exception):
                render(2)
                if isinstance(result, DownloadCancelled):
                    notice.configure(text="Загрузка отменена. Можно повторить её позже.")
                else:
                    notice.configure(text=f"Загрузка не завершена: {result}")
            else:
                render(4)

        def escape(_event: tk.Event[Any]) -> str:
            if state["step"] == 3:
                cancel_action = state.get("cancel_action")
                if cancel_action is not None:
                    cancel_action()
                return "break"
            finish()
            return "break"

        self._overlay_escape = escape
        render(0)

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

    def _submit(self, name: str, work: Callable[[], Any], done: Callable[[Any], None]) -> threading.Thread:
        self._set_busy(True, f"{name}…")

        def run() -> None:
            try:
                result: Any = work()
            except Exception as exc:
                result = exc
            self.events.put(("job_done", name, result, done))

        worker = threading.Thread(target=run, name=f"DotLingo {name}", daemon=True)
        worker.start()
        return worker

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
                        self._update_queue_row(project_id, task_event)
                    elif self.page == "documents" and task_event["status"] in {"complete", "failed", "cancelled", "interrupted"}:
                        self.show_page("documents")
                elif kind == "download_progress":
                    self._update_download_progress(event[1])
                elif kind == "wizard_progress":
                    self._update_wizard_progress(event[1])
        except queue.Empty:
            pass
        self.root.after(150, self._poll_events)

    def _update_download_progress(self, value: dict[str, Any]) -> None:
        if self._pending_close:
            self._show_close_wait_status()
            return
        if not self._download_window or not self._download_window.winfo_exists() or not hasattr(self, "_download_progress_widgets"):
            return
        label, progress = self._download_progress_widgets
        amount = int(value.get("bytes", 0))
        total = int(value.get("total", 0))
        phase = value.get("phase")
        if phase == "verifying":
            label.configure(text="Проверка SHA-256 и структуры модели · отмена ещё доступна")
            return
        if phase == "activating":
            progress.stop()
            label.configure(text="Проверки пройдены · активация файла · отмена больше недоступна")
            return
        if total > 0:
            progress.stop()
            progress.configure(mode="determinate", value=(amount * 100 / total))
            label.configure(text=f"Получено {_format_size(amount)} из {_format_size(total)} · ETA не показывается")
        else:
            if progress.cget("mode") != "indeterminate":
                progress.configure(mode="indeterminate")
                progress.start(12)
            label.configure(text=f"Получено {_format_size(amount)} · общий размер неизвестен · ETA не показывается")

    def _update_wizard_progress(self, value: dict[str, Any]) -> None:
        if self._pending_close:
            self._show_close_wait_status()
            return
        if not hasattr(self, "_wizard_progress"):
            return
        progress, label = self._wizard_progress
        try:
            amount = int(value.get("bytes", 0))
            total = int(value.get("total", 0))
            phase = value.get("phase")
            if phase == "verifying":
                label.configure(text="Проверка SHA-256 и структуры модели · отмена ещё доступна")
                return
            if phase == "activating":
                progress.stop()
                label.configure(text="Проверки пройдены · активация файла · отмена больше недоступна")
                return
            if total > 0:
                progress.stop()
                progress.configure(mode="determinate", maximum=100, value=(amount * 100 / total))
                label.configure(text=f"Получено {_format_size(amount)} из {_format_size(total)} · ETA не показывается")
            else:
                if progress.cget("mode") != "indeterminate":
                    progress.configure(mode="indeterminate")
                    progress.start(12)
                label.configure(text=f"Получено {_format_size(amount)} · общий размер неизвестен · ETA не показывается")
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
        if self._closing or self._pending_close:
            return
        if not self._confirm_review_change():
            return
        if self.page == "settings" and not self._confirm_settings_change():
            return
        worker = self._download_worker
        if worker is not None and worker.is_alive():
            self._pending_close = True
            self._close_cancel_accepted = self._download_cancel.set() if self._download_cancel else None
            self._show_close_wait_status()
            self._close_wait_job = self.root.after(100, self._wait_for_download_close)
            return
        self._finalize_close()

    def _show_close_wait_status(self) -> None:
        if self._close_cancel_accepted is True:
            message = "Отмена загрузки запрошена. Ожидается безопасное завершение…"
        elif self._close_cancel_accepted is False:
            message = "Модель уже активируется. Ожидается завершение проверки…"
        else:
            message = "Загрузка завершается. Приложение закроется после сохранения состояния…"
        try:
            self.sidebar_status.configure(text=message)
            if self._download_window is not None and self._download_window.winfo_exists():
                label = self._download_progress_widgets[0]
                label.configure(text=message)
            elif hasattr(self, "_wizard_progress"):
                self._wizard_progress[1].configure(text=message)
        except (AttributeError, tk.TclError):
            pass

    def _wait_for_download_close(self) -> None:
        self._close_wait_job = None
        if not self._pending_close or self._closing:
            return
        worker = self._download_worker
        if worker is not None and worker.is_alive():
            self._show_close_wait_status()
            self._close_wait_job = self.root.after(100, self._wait_for_download_close)
            return
        self._download_worker = None
        self._pending_close = False
        self._finalize_close()

    def _finalize_close(self) -> None:
        self._closing = True
        self._stop_page_animation()
        if self._close_wait_job is not None:
            try:
                self.root.after_cancel(self._close_wait_job)
            except tk.TclError:
                pass
            self._close_wait_job = None
        if self._overlay is not None and self._overlay.winfo_exists():
            self._close_overlay(self._overlay)
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
