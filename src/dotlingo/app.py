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
from dotlingo.hardware import HardwareSnapshot, assess_model, detect, recommend_model
from dotlingo.languages import (
    AUTO_LANGUAGE,
    AUTO_LANGUAGE_LABEL,
    LANGUAGES,
    language_display,
    language_label,
    supported_languages,
    supports_language,
)
from dotlingo.model_download import DownloadCancellation, DownloadCancelled, download_model
from dotlingo.models import all_models, import_custom_model, installed, model_path, verify_model
from dotlingo.paths import user_data_root
from dotlingo.preferences import load_preferences, save_preferences
from dotlingo.storage import ProjectStore, list_projects
from dotlingo.task_queue import TaskQueue, build_chunks
from dotlingo.theme import THEME_LABELS, TOKENS, set_theme

PAGES = (
    ("documents", "Перевод"),
    ("review", "Проверка"),
    ("queue", "Очередь"),
    ("projects", "Проекты"),
    ("models", "Модели"),
    ("glossary", "Глоссарий"),
    ("settings", "Настройки"),
)

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
        self._device_check_in_progress = False
        self._model_recommendation_id: str | None = None
        preferences = load_preferences(self.preferences_root)
        self.reduce_motion = bool(preferences["reduce_motion"])
        self.theme = set_theme(preferences.get("theme", "dark"))
        self._selected_doc = ""
        self._review_order: list[int] = []
        self._review_block: int | None = None
        self._review_target_lang = "ru"
        self._glossary_target_lang = "ru"
        self._review_text_original = ""
        self._review_block_orders: set[int] = set()
        self._review_progress_label: ttk.Label | None = None
        self._review_save_status: ttk.Label | None = None
        self._review_save_button: ttk.Button | None = None
        self._review_loading = False
        self._review_refilter: Callable[[], None] | None = None
        self._model_checks: dict[str, tk.BooleanVar] = {}
        self._download_cancel: DownloadCancellation | None = None
        self._download_worker: threading.Thread | None = None
        self._custom_model_worker: threading.Thread | None = None
        self._download_window: tk.Frame | None = None
        self._overlay: tk.Frame | None = None
        self._overlay_previous_focus: tk.Widget | None = None
        self._overlay_escape: Callable[[tk.Event[Any]], str | None] | None = None
        self._nav_indicator_jobs: dict[str, str] = {}
        self._page_title_job: str | None = None
        self._page_motion_job: str | None = None
        self.nav_indicators: dict[str, tk.Frame] = {}
        self.nav_icons: dict[str, tk.Canvas] = {}
        self._nav_icon_jobs: dict[str, str] = {}
        self._documents_tree: ttk.Treeview | None = None
        self._document_selection_label: ttk.Label | None = None
        self._settings_form: tuple[Any, ...] | None = None
        self._theme_var: tk.StringVar | None = None
        self._status_dot: tk.Canvas | None = None
        self._closing = False
        self._pending_close = False
        self._close_wait_job: str | None = None
        self._close_cancel_accepted: bool | None = None

        self.root.title("DotLingo · локальный перевод документов")
        self.root.geometry("1280x820")
        self.root.minsize(1020, 680)
        self._set_icon()
        self._style()
        self._build_shell()
        self._refresh_project_list()
        self._restore_active_project()
        if self.active is not None:
            self.show_page("documents")
        for index, (page, _) in enumerate(PAGES, start=1):
            self.root.bind(
                f"<Control-Key-{index}>",
                lambda _event, selected_page=page: self._navigate_shortcut(selected_page),
            )
        self.root.bind("<Control-f>", lambda _event: self._find_shortcut())
        self.root.bind("<Control-s>", lambda _event: self._save_review_shortcut())
        self.root.bind("<Escape>", self._handle_overlay_escape)
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self._poll_events()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        setup_seen = bool(load_preferences(self.preferences_root)["setup_seen"])
        if smoke:
            self.root.withdraw()
            self.root.after(80, self._smoke_check)
        elif not setup_seen:
            self.root.after(250, self.open_setup_wizard)
        else:
            self.root.after(300, self._detect_hardware)

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
        style.configure("Workspace.TFrame", background=colors["bg"])
        style.configure("Sidebar.TFrame", background=colors["sidebar"])
        style.configure("Panel.TFrame", background=colors["surface"], borderwidth=0, relief="flat")
        style.configure(
            "Glass.TFrame",
            background=colors["surface"],
            borderwidth=1,
            bordercolor=colors["border_soft"],
            lightcolor=colors["border_soft"],
            darkcolor=colors["border_soft"],
            relief="solid",
        )
        style.configure(
            "Sidebar.Context.TFrame",
            background=colors["surface"],
            borderwidth=1,
            bordercolor=colors["border_soft"],
            relief="solid",
        )
        style.configure("AccentRule.TFrame", background=colors["accent"])
        style.configure("Status.TFrame", background=colors["bg"])
        style.configure(
            "TLabel",
            background=colors["bg"],
            foreground=colors["text"],
            font=(colors["font"], colors["font_body"]),
        )
        style.configure(
            "Muted.TLabel",
            background=colors["bg"],
            foreground=colors["muted"],
            font=(colors["font"], colors["font_small"]),
        )
        style.configure(
            "Panel.Muted.TLabel",
            background=colors["surface"],
            foreground=colors["muted"],
            font=(colors["font"], colors["font_small"]),
        )
        style.configure(
            "Status.Muted.TLabel",
            background=colors["bg"],
            foreground=colors["muted"],
            font=(colors["font"], colors["font_small"]),
        )
        for label_style in ("Error.TLabel", "Panel.Error.TLabel"):
            style.configure(
                label_style,
                background=colors["surface"],
                foreground=colors["error"],
                font=(colors["font"], colors["font_small"]),
            )
        style.configure(
            "Panel.TLabel",
            background=colors["surface"],
            foreground=colors["text"],
            font=(colors["font"], colors["font_body"]),
        )
        style.configure(
            "Title.TLabel",
            background=colors["bg"],
            foreground=colors["text"],
            font=(colors["font"], colors["font_title"], "bold"),
        )
        style.configure(
            "Section.TLabel",
            background=colors["bg"],
            foreground=colors["text"],
            font=(colors["font"], colors["font_heading"], "bold"),
        )
        style.configure(
            "Panel.Section.TLabel",
            background=colors["surface"],
            foreground=colors["text"],
            font=(colors["font"], colors["font_heading"], "bold"),
        )
        style.configure(
            "Brand.TLabel",
            background=colors["bg"],
            foreground=colors["text"],
            font=(colors["font"], 17, "bold"),
        )
        style.configure(
            "Sidebar.Brand.TLabel",
            background=colors["sidebar"],
            foreground=colors["text"],
            font=(colors["font"], 17, "bold"),
        )
        style.configure(
            "Sidebar.Muted.TLabel",
            background=colors["sidebar"],
            foreground=colors["muted"],
            font=(colors["font"], colors["font_small"]),
        )
        style.configure(
            "Sidebar.Eyebrow.TLabel",
            background=colors["sidebar"],
            foreground=colors["muted"],
            font=(colors["mono"], 9),
        )
        style.configure(
            "Sidebar.Context.TLabel",
            background=colors["surface"],
            foreground=colors["text"],
            font=(colors["font"], colors["font_body"], "bold"),
        )
        style.configure(
            "Sidebar.Context.Muted.TLabel",
            background=colors["surface"],
            foreground=colors["muted"],
            font=(colors["font"], colors["font_small"]),
        )
        style.configure(
            "Header.Kicker.TLabel",
            background=colors["bg"],
            foreground=colors["accent"],
            font=(colors["mono"], 10, "bold"),
        )
        style.configure(
            "Header.Index.TLabel",
            background=colors["bg"],
            foreground=colors["muted"],
            font=(colors["mono"], 10),
        )
        style.configure(
            "Hero.Title.TLabel",
            background=colors["surface"],
            foreground=colors["text"],
            font=(colors["font"], 24, "bold"),
        )
        style.configure(
            "Hero.Step.TLabel",
            background=colors["surface"],
            foreground=colors["accent"],
            font=(colors["mono"], 10, "bold"),
        )
        style.configure(
            "Eyebrow.TLabel",
            background=colors["bg"],
            foreground=colors["accent"],
            font=(colors["font"], colors["font_small"], "bold"),
        )
        style.configure(
            "Panel.Eyebrow.TLabel",
            background=colors["surface"],
            foreground=colors["accent"],
            font=(colors["font"], colors["font_small"], "bold"),
        )
        style.configure(
            "TButton",
            background=colors["surface_raised"],
            foreground=colors["text"],
            padding=(13, 9),
            borderwidth=1,
            bordercolor=colors["border"],
            focusthickness=2,
            focuscolor=colors["border_focus"],
            font=(colors["font"], colors["font_body"]),
        )
        style.map(
            "TButton",
            background=[("disabled", colors["surface"]), ("pressed", colors["highlight"]), ("active", colors["surface_hover"])],
            foreground=[("disabled", colors["muted"])],
            bordercolor=[("focus", colors["border_focus"])],
        )
        style.configure(
            "Accent.TButton",
            background=colors["accent"],
            foreground=colors["accent_on"],
            padding=(16, 10),
            borderwidth=0,
            font=(colors["font"], colors["font_body"], "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("disabled", colors["border"]), ("pressed", colors["accent_dim"]), ("active", colors["accent_dim"])],
            foreground=[("disabled", colors["muted"]), ("pressed", colors["accent_on"]), ("active", colors["accent_on"])],
            bordercolor=[("focus", colors["border_focus"])],
        )
        style.configure(
            "Nav.TButton",
            background=colors["sidebar"],
            foreground=colors["muted"],
            anchor="w",
            padding=(9, 10),
            borderwidth=0,
            font=(colors["font"], colors["font_body"]),
        )
        style.map(
            "Nav.TButton",
            background=[("pressed", colors["surface_hover"]), ("active", colors["surface_hover"])],
            foreground=[("focus", colors["text"]), ("active", colors["text"])],
        )
        style.configure(
            "ActiveNav.TButton",
            background=colors["nav_active"],
            foreground=colors["text"],
            anchor="w",
            padding=(9, 10),
            borderwidth=0,
            font=(colors["font"], colors["font_body"], "bold"),
        )
        style.map(
            "ActiveNav.TButton",
            background=[("pressed", colors["nav_active"]), ("active", colors["nav_active"])],
            foreground=[("active", colors["text"]), ("focus", colors["text"])],
            bordercolor=[("focus", colors["border_focus"])],
        )
        style.configure(
            "TEntry",
            fieldbackground=colors["surface_raised"],
            foreground=colors["text"],
            insertcolor=colors["accent"],
            padding=(11, 9),
            borderwidth=1,
            bordercolor=colors["border"],
            lightcolor=colors["border"],
            darkcolor=colors["border"],
            font=(colors["font"], colors["font_body"]),
        )
        style.map(
            "TEntry",
            fieldbackground=[("disabled", colors["surface"]), ("focus", colors["surface_raised"])],
            bordercolor=[("focus", colors["border_focus"])],
        )
        style.configure(
            "TCombobox",
            fieldbackground=colors["surface_raised"],
            background=colors["surface_raised"],
            foreground=colors["text"],
            arrowcolor=colors["muted"],
            padding=(10, 8),
            borderwidth=1,
            bordercolor=colors["border"],
            lightcolor=colors["border"],
            darkcolor=colors["border"],
            font=(colors["font"], colors["font_body"]),
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", colors["surface_raised"]), ("disabled", colors["surface"])],
            foreground=[("disabled", colors["muted"])],
            bordercolor=[("focus", colors["border_focus"])],
        )
        for widget_style in ("TCheckbutton", "TRadiobutton"):
            style.configure(
                widget_style,
                background=colors["surface"],
                foreground=colors["text"],
                padding=(4, 6),
                font=(colors["font"], colors["font_body"]),
            )
            style.map(
                widget_style,
                background=[("active", colors["surface"])],
                foreground=[("disabled", colors["muted"])],
                indicatorcolor=[("selected", colors["accent"]), ("!selected", colors["surface_raised"])],
            )
        style.configure(
            "Treeview",
            background=colors["surface"],
            fieldbackground=colors["surface"],
            foreground=colors["text"],
            rowheight=44,
            borderwidth=0,
            font=(colors["font"], colors["font_body"]),
        )
        style.map(
            "Treeview",
            background=[("selected", colors["highlight"])],
            foreground=[("selected", colors["text"])],
        )
        style.configure(
            "Treeview.Heading",
            background=colors["surface"],
            foreground=colors["muted"],
            padding=(10, 10),
            borderwidth=0,
            font=(colors["font"], colors["font_small"], "bold"),
        )
        style.map(
            "Treeview.Heading",
            background=[("active", colors["surface_hover"])],
            foreground=[("active", colors["text"])],
        )
        style.configure("TNotebook", background=colors["bg"], borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background=colors["surface"],
            foreground=colors["muted"],
            padding=(14, 9),
            font=(colors["font"], colors["font_body"]),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", colors["surface_raised"])],
            foreground=[("selected", colors["text"])],
        )
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor=colors["border_soft"],
            background=colors["accent"],
            bordercolor=colors["border_soft"],
            thickness=8,
        )
        for scrollbar_style in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
            style.configure(
                scrollbar_style,
                background=colors["border"],
                troughcolor=colors["surface"],
                bordercolor=colors["surface"],
                arrowcolor=colors["muted"],
                gripcount=0,
                width=10,
            )
        style.configure("TSeparator", background=colors["border_soft"])

    def _change_theme(self, _event: tk.Event[Any] | None = None) -> None:
        if self._theme_var is None:
            return
        selected = next(
            (name for name, label in THEME_LABELS.items() if label == self._theme_var.get()),
            "dark",
        )
        if selected == self.theme:
            return
        preferences = load_preferences(self.preferences_root)
        preferences["theme"] = selected
        try:
            save_preferences(preferences, self.preferences_root)
        except OSError as exc:
            self._theme_var.set(THEME_LABELS[self.theme])
            self.sidebar_status.configure(text=f"Не удалось сохранить тему: {exc}")
            return

        self.theme = set_theme(selected)
        self._style()
        self._refresh_theme_widgets()
        self.sidebar_status.configure(text=f"Включена {THEME_LABELS[selected].lower()} тема")

    def _refresh_theme_widgets(self) -> None:
        self._stop_page_animation()
        self._title_rule.configure(width=68)
        self.header_title.configure(foreground=TOKENS["text"])
        if self._status_dot is not None and self._status_dot.winfo_exists():
            self._status_dot.configure(background=TOKENS["sidebar"])
            self._status_dot.itemconfigure("all", fill=TOKENS["success"])
        brand_mark = getattr(self, "_brand_mark", None)
        if brand_mark is not None and brand_mark.winfo_exists():
            brand_mark.configure(background=TOKENS["sidebar"])
            brand_mark.itemconfigure(
                "brand-frame", fill=TOKENS["surface"], outline=TOKENS["border"]
            )
            brand_mark.itemconfigure("brand-mark", fill=TOKENS["accent"])
        self._stop_nav_animation()
        for page, indicator in self.nav_indicators.items():
            if indicator.winfo_exists():
                indicator.configure(
                    background=TOKENS["accent"] if page == self.page else TOKENS["sidebar"]
                )

        tag_colors = {
            "complete": TOKENS["success"],
            "in_progress": TOKENS["accent"],
            "queued": TOKENS["muted"],
            "running": TOKENS["accent"],
            "paused": TOKENS["warning"],
            "failed": TOKENS["error"],
            "cancelled": TOKENS["muted"],
            "interrupted": TOKENS["warning"],
        }

        def recolor(parent: tk.Misc) -> None:
            try:
                children = parent.winfo_children()
            except tk.TclError:
                return
            for widget in children:
                try:
                    if isinstance(widget, tk.Text):
                        widget.configure(
                            background=TOKENS["surface_raised"],
                            foreground=TOKENS["text"],
                            insertbackground=TOKENS["accent"],
                            selectbackground=TOKENS["highlight"],
                            highlightbackground=TOKENS["border"],
                            highlightcolor=TOKENS["border_focus"],
                        )
                    elif isinstance(widget, tk.Listbox):
                        widget.configure(
                            background=TOKENS["surface_raised"],
                            foreground=TOKENS["text"],
                            selectbackground=TOKENS["highlight"],
                            selectforeground=TOKENS["text"],
                            highlightbackground=TOKENS["border"],
                            highlightcolor=TOKENS["border_focus"],
                        )
                    elif isinstance(widget, tk.Canvas):
                        nav_page = next(
                            (page for page, icon in self.nav_icons.items() if icon is widget),
                            None,
                        )
                        widget.configure(
                            background=(
                                TOKENS["sidebar"]
                                if widget in (
                                    self._status_dot,
                                    getattr(self, "_brand_mark", None),
                                )
                                or nav_page is not None
                                else TOKENS["bg"]
                            ),
                            highlightbackground=(
                                TOKENS["sidebar"]
                                if widget in (
                                    self._status_dot,
                                    getattr(self, "_brand_mark", None),
                                )
                                or nav_page is not None
                                else TOKENS["bg"]
                            ),
                        )
                        if nav_page is not None:
                            color = (
                                TOKENS["text"] if nav_page == self.page else TOKENS["muted"]
                            )
                            self._draw_nav_icon(widget, nav_page, color)
                    elif isinstance(widget, tk.Frame):
                        widget.configure(
                            background=(
                                TOKENS["sidebar"]
                                if widget in self.nav_indicators.values()
                                else TOKENS["bg"]
                            )
                        )
                    elif isinstance(widget, tk.Toplevel):
                        widget.configure(background=TOKENS["bg"])

                    if isinstance(widget, ttk.Treeview):
                        for tag in widget.tag_names():
                            if tag in tag_colors:
                                widget.tag_configure(tag, foreground=tag_colors[tag])
                except tk.TclError:
                    pass
                recolor(widget)

        recolor(self.root)
        for page, indicator in self.nav_indicators.items():
            if indicator.winfo_exists():
                indicator.configure(
                    background=TOKENS["accent"] if page == self.page else TOKENS["sidebar"]
                )
        if self.page == "queue":
            self._update_queue_detail()
        elif self.page == "review":
            self._update_review_edit_feedback()

    def _build_shell(self) -> None:
        self.root.configure(background=TOKENS["bg"])
        outer = ttk.Frame(self.root, style="Workspace.TFrame")
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(outer, style="Sidebar.TFrame", padding=(18, 22, 18, 16))
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.configure(width=238)
        sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(2, weight=1)

        brand_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        brand_row.grid(row=0, column=0, sticky="ew", pady=(0, 28))
        brand_row.columnconfigure(1, weight=1)
        mark = tk.Canvas(
            brand_row,
            width=38,
            height=38,
            background=TOKENS["sidebar"],
            highlightthickness=0,
        )
        mark.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 12))
        mark.create_rectangle(
            3,
            3,
            35,
            35,
            fill=TOKENS["surface"],
            outline=TOKENS["border"],
            width=1,
            tags=("brand-frame",),
        )
        mark.create_line(11, 10, 24, 10, fill=TOKENS["accent"], width=2, tags="brand-mark")
        mark.create_line(11, 16, 24, 16, fill=TOKENS["accent"], width=2, tags="brand-mark")
        mark.create_line(11, 22, 20, 22, fill=TOKENS["accent"], width=2, tags="brand-mark")
        mark.create_line(11, 28, 21, 28, fill=TOKENS["accent"], width=2, tags="brand-mark")
        self._brand_mark = mark
        ttk.Label(brand_row, text="dotlingo", style="Sidebar.Brand.TLabel").grid(
            row=0, column=1, sticky="sw"
        )
        ttk.Label(
            brand_row,
            text="ПЕРЕВОД ДОКУМЕНТОВ",
            style="Sidebar.Muted.TLabel",
        ).grid(row=1, column=1, sticky="nw", pady=(1, 0))

        ttk.Label(sidebar, text="НАВИГАЦИЯ / 01", style="Sidebar.Eyebrow.TLabel").grid(
            row=1, column=0, sticky="w", pady=(0, 8)
        )
        self._theme_var = tk.StringVar(value=THEME_LABELS[self.theme])
        theme_combo = ttk.Combobox(
            sidebar,
            textvariable=self._theme_var,
            values=tuple(THEME_LABELS.values()),
            state="readonly",
            width=11,
        )
        theme_combo.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        theme_combo.bind("<<ComboboxSelected>>", self._change_theme)

        navigation = ttk.Frame(sidebar, style="Sidebar.TFrame")
        navigation.grid(row=2, column=0, sticky="nsew")
        navigation.columnconfigure(0, weight=1)
        self.nav_buttons: dict[str, ttk.Button] = {}
        for key, label in PAGES:
            row = ttk.Frame(navigation, style="Sidebar.TFrame")
            row.grid(row=index, column=0, sticky="ew", pady=2)
            row.columnconfigure(2, weight=1)
            indicator = tk.Frame(row, background=TOKENS["sidebar"], width=3, height=24)
            indicator.grid(row=0, column=0, sticky="ns", padx=(0, 4))
            icon = tk.Canvas(
                row,
                width=22,
                height=22,
                background=TOKENS["sidebar"],
                highlightthickness=0,
                takefocus=False,
            )
            icon.grid(row=0, column=1, sticky="w", padx=(5, 8))
            self.nav_icons[key] = icon
            self._draw_nav_icon(icon, key, TOKENS["muted"])
            button = ttk.Button(
                row,
                text=label,
                style="Nav.TButton",
                command=lambda page=key: self.show_page(page),
            )
            button.grid(row=0, column=2, sticky="ew")
            button.configure(takefocus=True)
            self.nav_buttons[key] = button
            self.nav_indicators[key] = indicator

        project_context = ttk.Frame(sidebar, style="Sidebar.Context.TFrame", padding=12)
        project_context.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        project_context.columnconfigure(0, weight=1)
        ttk.Label(
            project_context,
            text="ТЕКУЩИЙ ПРОЕКТ",
            style="Sidebar.Context.Muted.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.sidebar_project = ttk.Label(
            project_context,
            text="Проект не выбран",
            style="Sidebar.Context.TLabel",
            wraplength=180,
        )
        self.sidebar_project.grid(row=1, column=0, sticky="w", pady=(5, 0))

        status_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        status_row.grid(row=5, column=0, sticky="ew", pady=(14, 0))
        ttk.Separator(status_row).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        status_row.columnconfigure(1, weight=1)
        status_dot = tk.Canvas(
            status_row,
            width=9,
            height=9,
            background=TOKENS["sidebar"],
            highlightthickness=0,
        )
        status_dot.grid(row=1, column=0, sticky="nw", padx=(0, 8), pady=(4, 0))
        status_dot.create_oval(1, 1, 8, 8, fill=TOKENS["success"], outline="")
        self._status_dot = status_dot
        self.sidebar_status = ttk.Label(
            status_row,
            text="Локально и приватно",
            style="Sidebar.Muted.TLabel",
            anchor="w",
            wraplength=155,
        )
        self.sidebar_status.grid(row=1, column=1, sticky="ew")

        main = ttk.Frame(outer, style="Workspace.TFrame", padding=(40, 30, 40, 28))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)
        self.project_header = ttk.Frame(main, style="Workspace.TFrame")
        self.project_header.grid(row=0, column=0, sticky="ew", pady=(0, 24))
        self.project_header.columnconfigure(0, weight=1)
        ttk.Label(
            self.project_header,
            text="DOTLINGO  /  РАБОЧЕЕ ПРОСТРАНСТВО",
            style="Header.Kicker.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.header_index = ttk.Label(
            self.project_header,
            text="04 / 07",
            style="Header.Index.TLabel",
        )
        self.header_index.grid(row=0, column=1, sticky="e")
        self.header_title = ttk.Label(self.project_header, text="Проекты", style="Title.TLabel")
        self.header_title.grid(row=1, column=0, columnspan=2, sticky="w")
        self.header_project = ttk.Label(
            self.project_header,
            text="Локальный перевод · без облачной обработки",
            style="Muted.TLabel",
            wraplength=640,
        )
        self.header_project.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self._title_rule = ttk.Frame(
            self.project_header, style="AccentRule.TFrame", width=68, height=2
        )
        self._title_rule.grid(row=3, column=0, columnspan=2, sticky="w", pady=(17, 0))
        self._title_rule.grid_propagate(False)

        self.page_host = ttk.Frame(main, style="Workspace.TFrame")
        self.page_host.grid(row=1, column=0, sticky="nsew")
        self.page_host.rowconfigure(0, weight=1)
        self.page_host.columnconfigure(0, weight=1)
        self.content = ttk.Frame(self.page_host)
        self.content.grid(row=0, column=0, sticky="nsew")
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

        self.show_page("projects")

    def _panel(self, parent: tk.Misc, **kwargs: Any) -> ttk.Frame:
        return ttk.Frame(
            parent,
            style="Glass.TFrame",
            padding=kwargs.pop("padding", TOKENS["spacing_md"]),
            **kwargs,
        )

    @staticmethod
    def _draw_nav_icon(canvas: tk.Canvas, page: str, color: str) -> None:
        canvas.delete("all")
        tags = ("glyph",)

        def line(*coords: int, width: float = 1.5) -> None:
            canvas.create_line(
                *coords,
                fill=color,
                width=width,
                capstyle="round",
                joinstyle="round",
                tags=tags,
            )

        if page == "documents":
            canvas.create_rectangle(5, 2, 17, 20, outline=color, width=1.5, tags=tags)
            line(8, 8, 14, 8)
            line(8, 11, 14, 11)
            line(8, 14, 13, 14)
        elif page == "review":
            canvas.create_rectangle(3, 3, 15, 19, outline=color, width=1.5, tags=tags)
            line(6, 8, 12, 8)
            line(6, 11, 10, 11)
            canvas.create_oval(11, 11, 19, 19, outline=color, width=1.5, tags=tags)
            line(17, 17, 20, 20)
        elif page == "queue":
            for y in (5, 11, 17):
                canvas.create_oval(3, y - 1, 5, y + 1, fill=color, outline=color, tags=tags)
                line(8, y, 19, y)
        elif page == "projects":
            line(2, 7, 8, 7, 10, 9, 20, 9, 20, 18, 2, 18, 2, 7)
            line(3, 9, 19, 9)
        elif page == "models":
            for y in (4, 10, 16):
                canvas.create_rectangle(4, y, 18, y + 3, outline=color, width=1.3, tags=tags)
        elif page == "glossary":
            line(4, 6, 4, 5, 8, 5, 8, 8, 5, 10, width=1.6)
            line(12, 6, 12, 5, 16, 5, 16, 8, 13, 10, width=1.6)
            line(4, 14, 18, 14)
            line(4, 18, 15, 18)
        elif page == "settings":
            canvas.create_oval(5, 5, 17, 17, outline=color, width=1.5, tags=tags)
            canvas.create_oval(9, 9, 13, 13, outline=color, width=1.5, tags=tags)
            for x1, y1, x2, y2 in (
                (11, 2, 11, 5),
                (11, 17, 11, 20),
                (2, 11, 5, 11),
                (17, 11, 20, 11),
            ):
                line(x1, y1, x2, y2)

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
            padx=14,
            pady=12,
            spacing1=2,
            spacing2=5,
            spacing3=3,
            font=(TOKENS["font"], TOKENS["font_body"] + 1),
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
        self._review_save_status = None
        self._review_save_button = None
        self._review_refilter = None
        self._documents_tree = None
        self._document_selection_label = None
        self._project_tree = None
        self._queue_tree = None
        self._queue_controls = None
        for child in self.content.winfo_children():
            child.destroy()

    def show_page(self, page: str) -> None:
        if page not in dict(PAGES):
            return
        previous_page = self.page
        if self.page == "review" and not self._confirm_review_change():
            return
        if self.page == "settings" and not self._confirm_settings_change():
            return
        self._stop_nav_animation()
        self._stop_page_animation()
        self.page = page
        labels = dict(PAGES)
        self.header_title.configure(text=labels[page])
        self.header_project.configure(text=self._project_context_text(self.active))
        self.header_index.configure(text=f"{list(labels).index(page) + 1:02} / {len(labels):02}")
        project_title = self.active.project["title"] if self.active is not None else "Проект не выбран"
        self.sidebar_project.configure(
            text=project_title if len(project_title) <= 44 else f"{project_title[:43]}…"
        )
        for key, button in self.nav_buttons.items():
            active = key == page
            button.configure(style="ActiveNav.TButton" if active else "Nav.TButton")
            self._draw_nav_icon(
                self.nav_icons[key],
                key,
                TOKENS["text"] if active else TOKENS["muted"],
            )
            self._animate_nav_indicator(key, key == page)
        if page != previous_page:
            self._animate_nav_icon(page)
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
        for index in range(8):
            self.content.rowconfigure(index, weight=0)
        for index in range(4):
            self.content.columnconfigure(index, weight=0)
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)
        renderers[page]()
        self._animate_page_title()
        self._animate_page_content()

    def _stop_page_animation(self) -> None:
        for name in ("_page_title_job", "_page_motion_job"):
            job = getattr(self, name, None)
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
                setattr(self, name, None)
        if hasattr(self, "content") and self.content.winfo_exists():
            self.content.grid_configure(padx=0, pady=0)

    def _animate_page_content(self) -> None:
        if self.reduce_motion:
            self.content.grid_configure(padx=0, pady=0)
            return
        self.content.grid_configure(pady=(6, 0))

        def advance(step: int = 0) -> None:
            if self._closing or not self.content.winfo_exists():
                self._page_motion_job = None
                return
            progress = min(1.0, step / 6)
            eased = 1 - (1 - progress) ** 3
            self.content.grid_configure(pady=(round(6 * (1 - eased)), 0))
            if step >= 6:
                self._page_motion_job = None
                return
            self._page_motion_job = self.root.after(18, lambda: advance(step + 1))

        advance()

    def _animate_page_title(self) -> None:
        self.header_title.configure(foreground=TOKENS["text"])
        if self.reduce_motion:
            self._title_rule.configure(width=68)
            return
        self._title_rule.configure(width=12)

        def advance(step: int = 0) -> None:
            if self._closing or not self._title_rule.winfo_exists():
                self._page_title_job = None
                return
            progress = min(1.0, step / 8)
            eased = 1 - (1 - progress) ** 3
            self._title_rule.configure(width=round(12 + 48 * eased))
            if step >= 8:
                self._page_title_job = None
                return
            self._page_title_job = self.root.after(18, lambda: advance(step + 1))

        advance()

    def _navigate_shortcut(self, page: str) -> str:
        if self._overlay is not None and self._overlay.winfo_exists():
            return "break"
        self.show_page(page)
        return "break"

    def _find_shortcut(self) -> str:
        if self._overlay is None or not self._overlay.winfo_exists():
            self._focus_review_search()
        return "break"

    def _save_review_shortcut(self) -> str:
        if self.page == "review" and (self._overlay is None or not self._overlay.winfo_exists()):
            self._save_review_edit()
        return "break"

    def _animate_nav_indicator(self, page: str, active: bool) -> None:
        indicator = self.nav_indicators[page]
        previous = self._nav_indicator_jobs.pop(page, None)
        if previous is not None:
            try:
                self.root.after_cancel(previous)
            except tk.TclError:
                pass
        start = str(indicator.cget("background"))
        target = TOKENS["accent"] if active else TOKENS["sidebar"]
        if self.reduce_motion or start == target:
            indicator.configure(background=target)
            return

        steps = 7
        start_rgb = tuple(int(start[index : index + 2], 16) for index in (1, 3, 5))
        target_rgb = tuple(int(target[index : index + 2], 16) for index in (1, 3, 5))

        def advance(step: int) -> None:
            if self._closing or not indicator.winfo_exists():
                self._nav_indicator_jobs.pop(page, None)
                return
            progress = step / steps
            eased = 1 - (1 - progress) ** 3
            color = "#" + "".join(
                f"{round(start_rgb[index] + (target_rgb[index] - start_rgb[index]) * eased):02x}"
                for index in range(3)
            )
            indicator.configure(background=color)
            if step >= steps:
                self._nav_indicator_jobs.pop(page, None)
                return
            self._nav_indicator_jobs[page] = self.root.after(16, lambda: advance(step + 1))

        self._nav_indicator_jobs[page] = self.root.after(16, lambda: advance(1))

    def _animate_nav_icon(self, page: str) -> None:
        canvas = self.nav_icons[page]
        previous = self._nav_icon_jobs.pop(page, None)
        if previous is not None:
            try:
                self.root.after_cancel(previous)
            except tk.TclError:
                pass
        if self.reduce_motion:
            return

        offset = 2
        canvas.move("glyph", 0, offset)
        offset_value = [offset]

        def settle(step: int = 0) -> None:
            if self._closing or not canvas.winfo_exists():
                self._nav_icon_jobs.pop(page, None)
                return
            next_offset = round(2 * (1 - min(1.0, step / 5)) ** 2)
            canvas.move("glyph", 0, next_offset - offset_value[0])
            offset_value[0] = next_offset
            if step >= 5:
                self._nav_icon_jobs.pop(page, None)
                return
            self._nav_icon_jobs[page] = self.root.after(16, lambda: settle(step + 1))

        self._nav_icon_jobs[page] = self.root.after(16, lambda: settle(1))

    def _stop_nav_animation(self) -> None:
        for job in (*self._nav_indicator_jobs.values(), *self._nav_icon_jobs.values()):
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self._nav_indicator_jobs.clear()
        self._nav_icon_jobs.clear()

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

    def _catalog(self) -> list[dict[str, Any]]:
        return all_models(self.models_dir)

    def _recommended_model(self) -> tuple[dict[str, Any] | None, str]:
        models = self._catalog()
        installed_ids = {item["id"] for item in models if installed(item, self.models_dir)}
        if self.hardware is None:
            options = [
                item
                for item in models
                if item.get("status") == "available"
                and isinstance(item.get("estimated_ram_gb"), (int, float))
            ]
            options.sort(key=lambda item: float(item["estimated_ram_gb"]))
            return (
                (options[0], "Проверка устройства выполняется; пока выбрана самая компактная проверенная карточка.")
                if options
                else (None, "Нет доступных моделей для подбора.")
            )
        return recommend_model(self.hardware, models, installed_ids)

    def _sync_recommended_model(self) -> tuple[dict[str, Any] | None, str]:
        model, reason = self._recommended_model()
        self._model_recommendation_id = model["id"] if model else None
        return model, reason

    def _refresh_project_list(self) -> None:
        self.projects = list_projects(self.projects_dir)

    def _project_context_text(self, project: ProjectStore | None) -> str:
        if project is None:
            return "Локальный перевод · без облачной обработки"
        info = project.project
        source = "Авто" if info["source_lang"] == AUTO_LANGUAGE else info["source_lang"].upper()
        targets = project.target_languages()
        target_summary = ", ".join(code.upper() for code in targets[:3])
        if len(targets) > 3:
            target_summary += f" +{len(targets) - 3}"
        return f"{info['title']} · {source} → {target_summary}"

    def _restore_active_project(self) -> None:
        last = load_preferences(self.preferences_root).get("last_project", "")
        self.active = next((project for project in self.projects if project.project["id"] == last), None)
        if self.active is None and self.projects:
            self.active = self.projects[0]
        self.header_project.configure(text=self._project_context_text(self.active))

    def _select_project(self, project: ProjectStore) -> None:
        self.active = project
        self._selected_doc = ""
        preferences = load_preferences(self.preferences_root)
        preferences["last_project"] = project.project["id"]
        save_preferences(preferences, self.preferences_root)
        self.header_project.configure(text=self._project_context_text(project))
        self.show_page("documents")

    def _show_projects(self) -> None:
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)
        panel = self._panel(self.content, padding=18)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        heading_row = ttk.Frame(panel, style="Panel.TFrame")
        heading_row.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        heading_row.columnconfigure(0, weight=1)
        ttk.Label(
            heading_row,
            text=f"Недавние проекты  /  {len(self.projects):02}",
            style="Panel.Section.TLabel",
        ).grid(row=0, column=0, sticky="w")
        new_project_button = ttk.Button(heading_row, text="Новый перевод", style="Accent.TButton", command=self._new_project)
        new_project_button.grid(row=0, column=1, sticky="e")
        tree: ttk.Treeview | None = None
        if self.projects:
            panel.columnconfigure(1, weight=0)
            tree = ttk.Treeview(panel, columns=("direction", "documents", "updated"), show="tree headings", selectmode="browse")
            tree.heading("#0", text="Проект")
            tree.heading("direction", text="Языки")
            tree.heading("documents", text="Файлы")
            tree.heading("updated", text="Изменён")
            tree.column("#0", width=220, minwidth=130, stretch=True)
            tree.column("direction", width=125, minwidth=95, stretch=True)
            tree.column("documents", width=55, minwidth=45, stretch=False, anchor="center")
            tree.column("updated", width=85, minwidth=75, stretch=False)
            tree.grid(row=1, column=0, sticky="nsew")
            tree_scroll = ttk.Scrollbar(panel, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=tree_scroll.set)
            tree_scroll.grid(row=1, column=1, sticky="ns")
            tree.bind("<Double-1>", lambda _: self._open_tree_project(tree))
            tree.bind("<Return>", lambda _: self._open_tree_project(tree))
            for project in sorted(self.projects, key=lambda item: item.project["updated_at"], reverse=True):
                info = project.project
                targets = project.target_languages()
                source = "Авто" if info["source_lang"] == AUTO_LANGUAGE else info["source_lang"].upper()
                target_summary = ", ".join(item.upper() for item in targets[:2])
                if len(targets) > 2:
                    target_summary += f" +{len(targets) - 2}"
                tree.insert(
                    "",
                    "end",
                    iid=info["id"],
                    values=(
                        f"{source} → {target_summary}",
                        len(project.documents()),
                        info["updated_at"][:10],
                    ),
                    text=info["title"],
                )
            if self.active is not None and tree.exists(self.active.project["id"]):
                tree.selection_set(self.active.project["id"])
                tree.focus(self.active.project["id"])
        else:
            new_project_button.grid_remove()
            empty = ttk.Frame(panel, style="Panel.TFrame")
            empty.grid(row=1, column=0, columnspan=2, sticky="nsew")
            empty.columnconfigure(0, weight=1)
            empty.rowconfigure(0, weight=1)
            empty_copy = ttk.Frame(empty, style="Panel.TFrame")
            empty_copy.grid(row=0, column=0, padx=24, pady=24)
            ttk.Label(empty_copy, text="01 / НОВЫЙ ПРОЕКТ", style="Hero.Step.TLabel").pack(anchor="w")
            ttk.Label(
                empty_copy,
                text="Тексту нужен свой маршрут.",
                style="Hero.Title.TLabel",
            ).pack(anchor="w", pady=(12, 0))
            ttk.Label(
                empty_copy,
                text="Создайте проект, выберите языки и локальную модель. Затем добавьте документы и запустите перевод.",
                style="Panel.Muted.TLabel",
                justify="left",
                wraplength=460,
            ).pack(anchor="w", pady=(10, 24))
            ttk.Button(
                empty_copy, text="Создать первый проект  →", style="Accent.TButton",
                command=self._new_project
            ).pack(anchor="w")
            ttk.Separator(empty_copy).pack(fill="x", pady=(28, 16))
            ttk.Label(
                empty_copy,
                text="ПРОЕКТ  →  ДОКУМЕНТЫ  →  ПЕРЕВОД  →  ПРОВЕРКА",
                style="Panel.Muted.TLabel",
            ).pack(anchor="w")
        if tree is not None:
            open_button = ttk.Button(
                panel, text="Открыть проект", style="Accent.TButton",
                command=lambda: self._open_tree_project(tree)
            )
            open_button.grid(row=2, column=0, sticky="e", pady=(10, 0))
            open_button.configure(state="normal" if tree.selection() else "disabled")
            tree.bind("<<TreeviewSelect>>", lambda _event: open_button.configure(state="normal" if tree.selection() else "disabled"))
        self._project_tree = tree

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
        dialog.title("Новый перевод")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(background=TOKENS["bg"])
        dialog.geometry("840x700")
        dialog.minsize(740, 620)
        frame = ttk.Frame(dialog, padding=22)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)
        ttk.Label(frame, text="Новый перевод", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            frame,
            text="Выберите модель и языки. Источник можно определить автоматически.",
            style="Muted.TLabel",
            wraplength=670,
        ).grid(row=1, column=0, sticky="w", pady=(5, 14))

        project_card = self._panel(frame, padding=(15, 12))
        project_card.grid(row=2, column=0, sticky="ew", pady=(0, 9))
        project_card.columnconfigure(0, weight=1)
        ttk.Label(project_card, text="Название проекта · необязательно", style="Panel.Eyebrow.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        title = ttk.Entry(project_card)
        title.grid(row=1, column=0, sticky="ew", pady=(5, 1))

        options_card = self._panel(frame, padding=(15, 12))
        options_card.grid(row=3, column=0, sticky="nsew", pady=(0, 10))
        options_card.columnconfigure(0, weight=1)
        options_card.rowconfigure(4, weight=1)
        model_choices = [item for item in self._catalog() if item.get("status") == "available"]
        model_by_name = {item["name"]: item for item in model_choices}
        recommended, _reason = self._recommended_model()
        default_model = recommended or (model_choices[0] if model_choices else None)
        model_var = tk.StringVar(value=default_model["name"] if default_model else "")
        ttk.Label(options_card, text="Модель", style="Panel.Eyebrow.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        model_combo = ttk.Combobox(
            options_card,
            textvariable=model_var,
            state="readonly",
            values=tuple(model_by_name),
        )
        model_combo.grid(row=1, column=0, sticky="ew", pady=(5, 12))

        language_header = ttk.Frame(options_card, style="Panel.TFrame")
        language_header.grid(row=2, column=0, sticky="ew")
        language_header.columnconfigure(0, weight=1)
        ttk.Label(language_header, text="Исходный язык", style="Panel.Eyebrow.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        source_var = tk.StringVar(value=AUTO_LANGUAGE_LABEL)
        source_combo = ttk.Combobox(language_header, textvariable=source_var, state="readonly", width=30)
        source_combo.grid(row=1, column=0, sticky="w", pady=(5, 8))

        target_header = ttk.Frame(options_card, style="Panel.TFrame")
        target_header.grid(row=3, column=0, sticky="ew", pady=(2, 5))
        target_header.columnconfigure(0, weight=1)
        ttk.Label(target_header, text="Перевести на", style="Panel.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        target_count = ttk.Label(target_header, text="0 выбрано", style="Panel.Muted.TLabel")
        target_count.grid(row=0, column=1, sticky="e")
        language_panel = self._panel(options_card, padding=(9, 7))
        language_panel.grid(row=4, column=0, sticky="nsew")
        language_panel.columnconfigure(0, weight=1)
        language_panel.rowconfigure(2, weight=1)
        ttk.Label(language_panel, text="Найти язык", style="Panel.Muted.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        target_search_var = tk.StringVar()
        target_search = ttk.Entry(language_panel, textvariable=target_search_var)
        target_search.grid(row=1, column=0, sticky="ew", pady=(0, 7))
        language_canvas = tk.Canvas(
            language_panel,
            background=TOKENS["surface"],
            highlightthickness=0,
            height=210,
        )
        language_canvas.grid(row=2, column=0, sticky="nsew")
        language_scroll = ttk.Scrollbar(language_panel, orient="vertical", command=language_canvas.yview)
        language_scroll.grid(row=2, column=1, sticky="ns")
        language_canvas.configure(yscrollcommand=language_scroll.set)
        language_grid = ttk.Frame(language_canvas, style="Panel.TFrame")
        language_window = language_canvas.create_window((0, 0), window=language_grid, anchor="nw")
        language_grid.columnconfigure(0, weight=1)
        language_grid.bind(
            "<Configure>", lambda _event: language_canvas.configure(scrollregion=language_canvas.bbox("all"))
        )
        language_canvas.bind(
            "<Configure>", lambda event: language_canvas.itemconfigure(language_window, width=event.width - 4)
        )
        target_vars: dict[str, tk.BooleanVar] = {}
        target_widgets: dict[str, ttk.Checkbutton] = {}
        selected_targets: set[str] = set()
        rendered_model: str | None = None

        def update_target_count() -> None:
            target_count.configure(text=f"{len(selected_targets)} выбрано")

        def selected_source_code() -> str:
            if source_var.get() == AUTO_LANGUAGE_LABEL:
                return AUTO_LANGUAGE
            return next(
                (
                    code
                    for code in LANGUAGES
                    if language_display(code) == source_var.get()
                ),
                AUTO_LANGUAGE,
            )

        def update_excluded_language(_event: tk.Event[Any] | None = None) -> None:
            source_code = selected_source_code()
            selected_targets.discard(source_code)
            for code, widget in target_widgets.items():
                if code == source_code:
                    target_vars[code].set(False)
                    widget.configure(state="disabled")
                else:
                    widget.configure(state="normal")
            update_target_count()

        def render_languages(*_args: Any) -> None:
            nonlocal rendered_model
            for child in language_grid.winfo_children():
                child.destroy()
            target_vars.clear()
            target_widgets.clear()
            selected_model = model_by_name.get(model_var.get())
            if selected_model is None:
                return
            codes = supported_languages(selected_model)
            if rendered_model != model_var.get():
                selected_targets.intersection_update(codes)
                if not selected_targets and codes:
                    selected_targets.add("ru" if "ru" in codes else codes[0])
                rendered_model = model_var.get()
            source_choices = [AUTO_LANGUAGE_LABEL, *(language_display(code) for code in codes)]
            source_combo.configure(values=source_choices)
            if source_var.get() not in source_choices:
                source_var.set(AUTO_LANGUAGE_LABEL)
            query = target_search_var.get().strip().casefold()
            visible_codes = [
                code for code in codes if query in language_display(code).casefold()
            ]
            if not visible_codes:
                ttk.Label(
                    language_grid,
                    text="Совпадений нет",
                    style="Panel.Muted.TLabel",
                ).grid(row=0, column=0, sticky="w", pady=5)
            for index, code in enumerate(visible_codes):
                selected = code in selected_targets
                value = tk.BooleanVar(value=selected)

                def toggle_target(
                    language: str = code,
                    variable: tk.BooleanVar = value,
                ) -> None:
                    if variable.get():
                        selected_targets.add(language)
                    else:
                        selected_targets.discard(language)
                    update_target_count()

                target_vars[code] = value
                checkbox = ttk.Checkbutton(
                    language_grid,
                    text=language_display(code),
                    variable=value,
                    command=toggle_target,
                )
                checkbox.grid(row=index, column=0, sticky="ew", padx=(2, 6), pady=1)
                target_widgets[code] = checkbox
            update_target_count()
            update_excluded_language()

        def on_model_change(_event: tk.Event[Any] | None = None) -> None:
            render_languages()

        model_combo.bind("<<ComboboxSelected>>", on_model_change)
        source_combo.bind("<<ComboboxSelected>>", update_excluded_language)
        target_search.bind("<KeyRelease>", render_languages)
        render_languages()
        ttk.Label(
            options_card,
            text="Список языков зависит от модели. Качество конкретного направления может различаться.",
            style="Panel.Muted.TLabel",
            wraplength=660,
        ).grid(row=5, column=0, sticky="w", pady=(7, 0))

        def create() -> None:
            selected_model = model_by_name.get(model_var.get())
            source_code = AUTO_LANGUAGE if source_var.get() == AUTO_LANGUAGE_LABEL else next(
                (code for code in supported_languages(selected_model or {}) if language_display(code) == source_var.get()),
                "",
            )
            targets = [
                code for code in supported_languages(selected_model or {}) if code in selected_targets
            ]
            if selected_model is None:
                messagebox.showerror("Выберите модель", "Для проекта нужна доступная модель.", parent=dialog)
                return
            if not targets:
                messagebox.showerror("Выберите язык", "Отметьте хотя бы один язык перевода.", parent=dialog)
                return
            if source_code and source_code in targets:
                messagebox.showerror("Языки совпадают", "Исходный язык совпадает с целевым.", parent=dialog)
                return
            project = ProjectStore.create(
                self.projects_dir,
                title.get().strip(),
                source_code or AUTO_LANGUAGE,
                targets[0],
                target_langs=targets,
                model_id=selected_model["id"],
            )
            self._refresh_project_list()
            self._select_project(project)
            dialog.destroy()

        footer = ttk.Frame(frame)
        footer.grid(row=4, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(
            footer,
            text="Для перевода нужны установленная модель и runtime.",
            style="Muted.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="Создать проект", style="Accent.TButton", command=create).grid(
            row=0, column=1, sticky="e"
        )
        title.focus_set()
        dialog.bind("<Return>", lambda _: create())

    def _show_documents(self) -> None:
        store = self._active_store()
        if not store:
            self._need_project()
            return
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=0)
        self.content.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(self.content, style="Workspace.TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(toolbar, text="Файлы проекта", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(toolbar, text="Оригиналы хранятся отдельно от переводов.", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(3, 0))
        ttk.Button(toolbar, text="Добавить файлы", style="Accent.TButton", command=self._import_files).grid(row=0, column=1, rowspan=2, sticky="e", padx=(12, 0))

        documents = store.documents()
        if not documents:
            empty = self._panel(self.content, padding=24)
            empty.grid(row=1, column=0, sticky="nsew")
            empty.columnconfigure(0, weight=1)
            empty.rowconfigure(0, weight=1)
            content = ttk.Frame(empty, style="Panel.TFrame")
            content.grid(row=0, column=0)
            ttk.Label(content, text="02 / ДОКУМЕНТЫ", style="Hero.Step.TLabel").pack(anchor="w")
            ttk.Label(content, text="Добавьте исходные файлы.", style="Hero.Title.TLabel").pack(anchor="w", pady=(12, 0))
            ttk.Label(
                content,
                text="Поддерживаются TXT, Markdown, DOCX, EPUB и PDF с текстовым слоем. Оригиналы останутся отдельно от переводов.",
                style="Panel.Muted.TLabel",
                justify="left",
                wraplength=470,
            ).pack(anchor="w", pady=(10, 24))
            ttk.Button(content, text="Выбрать файлы  →", style="Accent.TButton", command=self._import_files).pack(anchor="w")
            return

        panel = self._panel(self.content, padding=15)
        panel.grid(row=1, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        panel.columnconfigure(1, weight=0)
        heading = ttk.Frame(panel, style="Panel.TFrame")
        heading.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        heading.columnconfigure(0, weight=1)
        ttk.Label(heading, text="Документы", style="Panel.Section.TLabel").grid(row=0, column=0, sticky="w")
        selection_label = ttk.Label(heading, text=f"{len(documents)} документов", style="Panel.Muted.TLabel")
        selection_label.grid(row=0, column=1, sticky="e")
        tree = ttk.Treeview(panel, columns=("format", "status", "exports"), show="tree headings", selectmode="extended")
        tree.heading("#0", text="Документ")
        tree.heading("format", text="Формат")
        tree.heading("status", text="Перевод")
        tree.heading("exports", text="Экспорт")
        tree.column("#0", width=220, minwidth=130, stretch=True)
        tree.column("format", width=70, minwidth=55, stretch=False)
        tree.column("status", width=130, minwidth=110, stretch=True)
        tree.column("exports", width=65, minwidth=55, stretch=False, anchor="center")
        tree.grid(row=1, column=0, sticky="nsew")
        tree_scroll = ttk.Scrollbar(panel, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.grid(row=1, column=1, sticky="ns")
        tree.tag_configure("complete", foreground=TOKENS["success"])
        tree.tag_configure("in_progress", foreground=TOKENS["accent"])
        target_languages = store.target_languages()
        for document in documents:
            blocks = [
                block
                for block in store.blocks(document.id)
                if block.translatable and block.text.strip()
            ]
            total = len(blocks)
            block_orders = {block.order for block in blocks}
            translated_count = sum(
                len(block_orders.intersection(store.translations(document.id, target_lang=target)))
                for target in target_languages
            )
            total *= len(target_languages)
            status = f"{translated_count} / {total} фрагментов" if total else "Нет текста для перевода"
            exports = len(store.exports(document.id))
            tag = "complete" if total and translated_count >= total else "in_progress" if translated_count else ""
            tree.insert("", "end", iid=document.id, text=document.name, values=(document.format.upper(), status, str(exports)), tags=(tag,))
        footer = ttk.Frame(panel, style="Panel.TFrame")
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        self._document_selection_label = ttk.Label(
            footer,
            text="Выберите файлы для перевода",
            style="Panel.Muted.TLabel",
            wraplength=320,
        )
        self._document_selection_label.grid(row=0, column=0, sticky="w")
        export_button = ttk.Button(footer, text="Открыть результат", command=self._open_latest_export, state="disabled")
        export_button.grid(row=0, column=1, padx=(8, 0))
        review_button = ttk.Button(footer, text="Проверить", command=lambda: self._open_selected_document(tree), state="disabled")
        review_button.grid(row=0, column=2, padx=(8, 0))
        translate_button = ttk.Button(footer, text="Перевести", style="Accent.TButton", command=self._enqueue_selected_documents, state="disabled")
        translate_button.grid(row=0, column=3, padx=(8, 0))
        self._documents_tree = tree

        def sync_actions(_event: tk.Event[Any] | None = None) -> None:
            self._set_selected_doc(tree)
            selected = tree.selection()
            count = len(selected)
            has_selection = count > 0
            translate_button.configure(state="normal" if has_selection else "disabled")
            review_button.configure(state="normal" if count == 1 else "disabled")
            has_export = bool(store.exports(self._selected_doc)) if count == 1 else False
            export_button.configure(state="normal" if has_export else "disabled")
            if count == 1:
                document = store.document(self._selected_doc)
                self._document_selection_label.configure(text=f"Выбран: {document.name}")
            elif count:
                self._document_selection_label.configure(text=f"Выбрано документов: {count}")
            else:
                self._document_selection_label.configure(text=f"{len(documents)} документов · исходники хранятся отдельно")

        tree.bind("<<TreeviewSelect>>", sync_actions)
        tree.bind("<Double-1>", lambda _event: self._open_selected_document(tree))
        self._documents_tree = tree

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
        model = next((item for item in self._catalog() if item["id"] == model_id), None)
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
        targets = store.target_languages()
        source = store.project["source_lang"]
        unsupported = [target for target in targets if not supports_language(model, target)]
        if source != AUTO_LANGUAGE and not supports_language(model, source):
            unsupported.insert(0, source)
        if unsupported:
            labels = ", ".join(language_label(code) for code in dict.fromkeys(unsupported))
            messagebox.showerror(
                "Языки недоступны для модели",
                f"Выбранная модель не перечисляет поддержку: {labels}. Измените модель или языки проекта.",
                parent=self.root,
            )
            return
        warning_lines = []
        for document in documents:
            warning_lines.extend(f"• {document.name}: {warning}" for warning in document.warnings)
        task_count = len(documents) * len(targets)
        target_names = ", ".join(language_label(code) for code in targets)
        source_name = "автоматическое определение" if source == AUTO_LANGUAGE else language_label(source)
        summary = (
            f"Будет создано задач: {task_count} · документов: {len(documents)} · "
            f"языков перевода: {len(targets)}.\n"
            f"Источник: {source_name}. Целевые языки: {target_names}.\n"
            "ETA не рассчитывается."
        )
        if warning_lines:
            summary += "\n\nОграничения формата:\n" + "\n".join(warning_lines[:8])
        summary += "\n\nЗапустить локальный перевод сейчас?"
        if not messagebox.askyesno("Подтверждение перевода", summary, parent=self.root):
            return
        task_queue = self._queue_for(store)
        try:
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
                    task_queue.enqueue(task_id)
        except Exception as exc:
            messagebox.showerror("Не удалось поставить задачу", str(exc), parent=self.root)
        self.show_page("queue")

    def _selected_document_ids(self) -> set[str]:
        tree = self._documents_tree
        if tree is not None and tree.winfo_exists():
            return set(tree.selection())
        return {self._selected_doc} if self._selected_doc else set()

    def _show_queue(self) -> None:
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)
        panel = self._panel(self.content)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        ttk.Label(panel, text="Прогресс показывает готовые фрагменты. Время до завершения не оценивается.", style="Panel.TLabel", wraplength=720).grid(row=0, column=0, sticky="w", pady=(0, 10))
        columns = ("document", "direction", "status", "progress", "model", "error")
        tree = ttk.Treeview(panel, columns=columns, show="headings", selectmode="browse")
        for key, label in zip(
            columns,
            ("Документ", "Направление", "Статус", "Фрагменты", "Модель", "Сообщение"),
        ):
            tree.heading(key, text=label)
        tree.column("document", width=200, minwidth=120, stretch=True)
        tree.column("direction", width=140, minwidth=100, stretch=True)
        tree.column("status", width=100, minwidth=80, stretch=False)
        tree.column("progress", width=95, minwidth=75, stretch=False)
        tree.column("model", width=170, minwidth=125, stretch=True)
        tree.column("error", width=240, minwidth=100, stretch=True)
        tree.grid(row=1, column=0, sticky="nsew")
        for project in self.projects:
            for task in project.tasks():
                task_id = task["id"]
                tree.insert("", "end", iid=task_id, values=self._queue_values(project, task), tags=(task["status"],))
        tree.tag_configure("queued", foreground=TOKENS["muted"])
        tree.tag_configure("running", foreground=TOKENS["accent"])
        tree.tag_configure("paused", foreground=TOKENS["warning"])
        tree.tag_configure("complete", foreground=TOKENS["success"])
        tree.tag_configure("failed", foreground=TOKENS["error"])
        tree.tag_configure("cancelled", foreground=TOKENS["muted"])
        tree.tag_configure("interrupted", foreground=TOKENS["warning"])

        detail = ttk.Frame(panel, style="Panel.TFrame")
        detail.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        detail.columnconfigure(1, weight=1)
        status_label = ttk.Label(detail, text="Задача не выбрана", style="Panel.TLabel", font=(TOKENS["font"], 10, "bold"))
        status_label.grid(row=0, column=0, sticky="w", padx=(0, 16))
        progress_label = ttk.Label(
            detail,
            text="Выберите перевод, чтобы посмотреть подробности.",
            style="Panel.Muted.TLabel",
            wraplength=760,
        )
        progress_label.grid(row=0, column=1, sticky="w")
        progress_bar = ttk.Progressbar(detail, mode="determinate", maximum=100, value=0)
        progress_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(9, 0))
        tree.bind("<<TreeviewSelect>>", lambda _event: self._update_queue_detail())

        button_row = ttk.Frame(panel, style="Panel.TFrame")
        button_row.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        resume_button = ttk.Button(button_row, text="Продолжить / повторить", command=lambda: self._queue_action(tree, "resume"), state="disabled")
        resume_button.pack(side="left")
        pause_button = ttk.Button(button_row, text="Пауза", command=lambda: self._queue_action(tree, "pause"), state="disabled")
        pause_button.pack(side="left", padx=7)
        cancel_button = ttk.Button(button_row, text="Отменить", command=lambda: self._queue_action(tree, "cancel"), state="disabled")
        cancel_button.pack(side="left")
        ttk.Button(button_row, text="Обновить", command=lambda: self.show_page("queue")).pack(side="right")
        self._queue_tree = tree
        self._queue_controls = {
            "status": status_label,
            "progress": progress_bar,
            "description": progress_label,
            "resume": resume_button,
            "pause": pause_button,
            "cancel": cancel_button,
        }
        self._update_queue_detail()

    def _queue_values(
        self, project: ProjectStore, task: dict[str, Any]
    ) -> tuple[str, str, str, str, str, str]:
        document = next((item for item in project.documents() if item.id == task["document_id"]), None)
        detected = project.detected_language(task["document_id"])
        source_label = task["source_lang"]
        if source_label == AUTO_LANGUAGE:
            source_label = f"{detected.upper()} · авто" if detected else "Авто"
        model_title = next(
            (item["name"] for item in self._catalog() if item["id"] == task["model_id"]),
            task["model_id"],
        )
        return (
            document.name if document else "Документ",
            f"{source_label} → {task['target_lang'].upper()}",
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
        self._update_queue_detail()

    def _update_queue_detail(self) -> None:
        controls = getattr(self, "_queue_controls", None)
        tree = getattr(self, "_queue_tree", None)
        if not controls or tree is None or not tree.winfo_exists():
            return
        selection = tree.selection()
        task = None
        project = None
        if selection:
            task_id = selection[0]
            for candidate in self.projects:
                try:
                    task = candidate.task(task_id)
                except KeyError:
                    continue
                project = candidate
                break
        if task is None or project is None:
            controls["status"].configure(text="Задача не выбрана", foreground=TOKENS["muted"])
            controls["description"].configure(text="Выберите перевод, чтобы посмотреть подробности.")
            controls["progress"].configure(value=0)
            for key in ("resume", "pause", "cancel"):
                controls[key].configure(state="disabled")
            return

        status = task["status"]
        colors = {
            "queued": TOKENS["muted"],
            "running": TOKENS["accent"],
            "paused": TOKENS["warning"],
            "complete": TOKENS["success"],
            "failed": TOKENS["error"],
            "cancelled": TOKENS["muted"],
            "interrupted": TOKENS["warning"],
        }
        document = next((item for item in project.documents() if item.id == task["document_id"]), None)
        model = next((item["name"] for item in self._catalog() if item["id"] == task["model_id"]), task["model_id"])
        completed = int(task["completed"])
        total = int(task["total"])
        amount = min(100, completed * 100 / total) if total else 0
        detected = project.detected_language(task["document_id"])
        source_label = task["source_lang"]
        if source_label == AUTO_LANGUAGE:
            source_label = f"{detected.upper()} · авто" if detected else "Авто"
        else:
            source_label = source_label.upper()
        direction = f"{source_label} → {task['target_lang'].upper()}"
        detail = (
            f"{document.name if document else 'Документ'} · {direction} · "
            f"{completed} из {total} фрагментов · {model}"
        )
        if task["error"]:
            detail += f" · {task['error']}"
        controls["status"].configure(text=self._status_label(status), foreground=colors.get(status, TOKENS["text"]))
        controls["description"].configure(text=detail)
        controls["progress"].configure(value=amount)
        controls["resume"].configure(state="normal" if status in {"paused", "interrupted", "failed", "cancelled"} else "disabled")
        controls["pause"].configure(state="normal" if status in {"queued", "running"} else "disabled")
        controls["cancel"].configure(state="normal" if status in {"queued", "running", "paused"} else "disabled")

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
            empty = self._panel(self.content, padding=24)
            empty.grid(row=0, column=0, sticky="nsew")
            empty.columnconfigure(0, weight=1)
            empty.rowconfigure(0, weight=1)
            message = ttk.Frame(empty, style="Panel.TFrame")
            message.grid(row=0, column=0)
            ttk.Label(message, text="В проекте пока нет документов", style="Panel.Section.TLabel").pack()
            ttk.Label(
                message,
                text="Добавьте файл, чтобы перейти к переводу и проверке.",
                style="Panel.Muted.TLabel",
            ).pack(pady=(6, 14))
            ttk.Button(
                message,
                text="Добавить файлы",
                style="Accent.TButton",
                command=self._import_files,
            ).pack()
            return
        self._selected_doc = document.id
        parsed = store.parsed(document.id)
        blocks = [block for block in parsed.blocks if block.translatable]
        target_languages = store.target_languages()
        if self._review_target_lang not in target_languages:
            self._review_target_lang = target_languages[0]
        translated = store.translations(document.id, target_lang=self._review_target_lang)
        complete = sum(1 for block in blocks if block.order in translated)
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=0)
        self.content.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(self.content)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(
            toolbar,
            text=document.name,
            style="Section.TLabel",
            wraplength=560,
        ).grid(row=0, column=0, sticky="w")
        target_language_var = tk.StringVar(value=language_display(self._review_target_lang))
        target_language_combo = ttk.Combobox(
            toolbar,
            textvariable=target_language_var,
            state="readonly",
            values=tuple(language_display(code) for code in target_languages),
            width=22,
        )
        target_language_combo.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self._review_progress_label = ttk.Label(toolbar, text=f"Переведено блоков: {complete}/{len(blocks)}", style="Muted.TLabel")
        self._review_progress_label.grid(row=1, column=0, sticky="w", pady=(6, 0))
        toolbar_actions = ttk.Frame(toolbar)
        toolbar_actions.grid(row=1, column=1, sticky="e", pady=(4, 0))
        self._review_save_status = ttk.Label(toolbar_actions, text="Сохранено", style="Muted.TLabel")
        self._review_save_status.pack(side="left", padx=(0, 8))
        self._review_save_button = ttk.Button(
            toolbar_actions,
            text="Сохранить",
            command=self._save_review_edit,
            state="disabled",
        )
        self._review_save_button.pack(side="left", padx=(0, 7))
        ttk.Button(
            toolbar_actions,
            text="Экспорт…",
            style="Accent.TButton",
            command=self._export_current,
        ).pack(side="left")

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
        target_text.bind("<<Modified>>", self._update_review_edit_feedback)
        self._review_block = None
        self._review_block_orders = {block.order for block in blocks}
        search_state: dict[str, Any] = {"job": None, "query": ""}

        groups: dict[str, list[Any]] = {}
        for block in blocks:
            groups.setdefault(block.section, []).append(block)

        def fill_tree(query: str = "") -> None:
            nonlocal translated
            lowered = query.casefold().strip()
            translated = store.translations(document.id, target_lang=self._review_target_lang)

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
                        translated = store.translations(
                            document.id, target_lang=self._review_target_lang
                        )
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
            current = store.translations(
                document.id, target_lang=self._review_target_lang
            ).get(order, "")
            target_text.insert("1.0", current)
            self._review_text_original = current
            target_text.edit_modified(False)
            self._review_loading = False
            self._update_review_edit_feedback()

        tree.bind("<<TreeviewSelect>>", lambda _: select_block(tree.selection()[0]) if tree.selection() else None)

        def change_target_language(_event: tk.Event[Any] | None = None) -> None:
            selected = next(
                (
                    code
                    for code in target_languages
                    if language_display(code) == target_language_var.get()
                ),
                self._review_target_lang,
            )
            if selected == self._review_target_lang:
                return
            if not self._confirm_review_change():
                target_language_var.set(language_display(self._review_target_lang))
                return
            current_order = self._review_block
            self._review_target_lang = selected
            translated = store.translations(document.id, target_lang=selected)
            complete = sum(1 for block in blocks if block.order in translated)
            self._review_progress_label.configure(
                text=f"Переведено блоков: {complete}/{len(blocks)}"
            )
            self._review_block = None
            fill_tree(search.get())
            current_iid = f"b{current_order}" if current_order is not None else ""
            if current_iid and tree.exists(current_iid):
                tree.selection_set(current_iid)
                tree.focus(current_iid)
                select_block(current_iid)

        target_language_combo.bind("<<ComboboxSelected>>", change_target_language)

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
        if event.widget is not self.root:
            return
        self.header_project.configure(wraplength=max(360, event.width - 320))
        if self.page != "review":
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
        orient = "vertical" if self.root.winfo_width() < 1120 else "horizontal"
        if body.cget("orient") != orient:
            body.configure(orient=orient)

            def position_sash() -> None:
                if not body.winfo_exists():
                    return
                try:
                    if orient == "vertical":
                        position = max(120, min(180, int(body.winfo_height() * 0.3)))
                    else:
                        position = max(190, int(body.winfo_width() * 0.22))
                    body.sashpos(0, position)
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
        if text == self._review_text_original:
            self._review_target.edit_modified(False)
            self._update_review_edit_feedback()
            return
        store.save_edit(
            self._selected_doc,
            self._review_block,
            text,
            target_lang=self._review_target_lang,
        )
        self._review_text_original = text
        self._review_target.edit_modified(False)
        translations = store.translations(
            self._selected_doc, target_lang=self._review_target_lang
        )
        if self.page == "review" and self._review_refilter is not None:
            try:
                self.root.after_idle(self._review_refilter)
            except tk.TclError:
                pass
        if self._review_progress_label is not None and self._review_progress_label.winfo_exists():
            complete = sum(1 for order in self._review_block_orders if order in translations)
            self._review_progress_label.configure(text=f"Переведено блоков: {complete}/{len(self._review_block_orders)}")
        self._update_review_edit_feedback()
        self.sidebar_status.configure(text="Правка перевода сохранена")

    def _update_review_edit_feedback(self, _event: tk.Event[Any] | None = None) -> None:
        if self._review_loading:
            return
        target = getattr(self, "_review_target", None)
        if target is None or not target.winfo_exists():
            return
        changed = target.get("1.0", "end-1c") != self._review_text_original
        if self._review_save_button is not None and self._review_save_button.winfo_exists():
            self._review_save_button.configure(state="normal" if changed else "disabled")
        if self._review_save_status is not None and self._review_save_status.winfo_exists():
            self._review_save_status.configure(
                text="Есть несохранённые изменения" if changed else "Сохранено",
                foreground=TOKENS["warning"] if changed else TOKENS["muted"],
            )

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
        target_languages = store.target_languages()
        target_lang = (
            self._review_target_lang
            if self._review_target_lang in target_languages
            else target_languages[0]
        )
        allowed = {
            "txt": (".txt", ".md"), "markdown": (".md", ".txt"), "docx": (".docx", ".txt", ".md"),
            "epub": (".epub", ".txt", ".md"), "pdf": (".txt", ".md"),
        }.get(parsed.format, (".txt",))
        extension = allowed[0]
        destination = filedialog.asksaveasfilename(
            parent=self.root,
            title="Экспорт перевода",
            initialdir=str(store.root / "output"),
            initialfile=f"{document.source_path.stem}.translated-{target_lang}{extension}",
            defaultextension=extension,
            filetypes=[("Поддерживаемый экспорт", " ".join(f"*{suffix}" for suffix in allowed))],
        )
        if not destination:
            return
        translations = store.translations(document.id, target_lang=target_lang)
        missing = sum(1 for block in parsed.blocks if block.translatable and block.text.strip() and block.order not in translations)
        if missing and not messagebox.askyesno("Перевод не завершён", f"Для {missing} блоков пока нет перевода. Экспортёр оставит их на исходном языке. Продолжить?", parent=self.root):
            return
        if Path(destination).suffix.lower() not in allowed:
            messagebox.showerror("Формат не поддерживается", "Выбранное расширение недоступно для этого исходного документа.", parent=self.root)
            return
        try:
            export_document(document.source_path, Path(destination), parsed, translations)
            store.record_export(document.id, Path(destination), target_lang=target_lang)
            messagebox.showinfo("Экспорт готов", f"Файл сохранён отдельно:\n{destination}", parent=self.root)
        except Exception as exc:
            messagebox.showerror("Ошибка экспорта", str(exc), parent=self.root)

    def _show_models(self) -> None:
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=0)
        self.content.rowconfigure(1, weight=0)
        self.content.rowconfigure(2, weight=1)
        top = ttk.Frame(self.content)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        top.columnconfigure(0, weight=1)
        ttk.Label(
            top,
            text="Каталог локальных моделей",
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(top, text="Проверить устройство", command=self._detect_hardware).grid(row=0, column=1, sticky="e", padx=(8, 0))
        ttk.Button(top, text="Добавить свою GGUF", command=self._add_custom_model).grid(row=0, column=2, sticky="e", padx=(8, 0))

        recommendation, recommendation_reason = self._recommended_model()
        suggestion = self._panel(self.content, padding=(16, 12))
        suggestion.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        suggestion.columnconfigure(0, weight=1)
        suggested_name = recommendation["name"] if recommendation else "Подбор пока недоступен"
        ttk.Label(suggestion, text="РЕКОМЕНДАЦИЯ ДЛЯ ЭТОГО УСТРОЙСТВА", style="Panel.Eyebrow.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(suggestion, text=suggested_name, style="Panel.Section.TLabel").grid(
            row=1, column=0, sticky="w", pady=(3, 1)
        )
        ttk.Label(
            suggestion,
            text=f"{recommendation_reason} Подбор оценивает совместимость по ресурсам, не качество перевода; текущий inference использует CPU.",
            style="Panel.Muted.TLabel",
            wraplength=760,
            justify="left",
        ).grid(row=2, column=0, sticky="w")
        if recommendation and self.active is not None:
            ttk.Button(
                suggestion,
                text="Выбрать для проекта",
                style="Accent.TButton",
                command=lambda model_id=recommendation["id"]: self._select_model_for_project(model_id),
            ).grid(row=0, column=1, rowspan=3, sticky="e", padx=(16, 0))

        container = ttk.Frame(self.content)
        container.grid(row=2, column=0, sticky="nsew")
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
        for index, model in enumerate(self._catalog()):
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
            language_count = len(supported_languages(model))
            language_line = (
                f"Заявленная поддержка: {model['languages']}\n"
                f"В выборе проекта: {language_count} языковых кода; отдельные пары не оценивались."
            )
            ttk.Label(
                card,
                text=f"{requirement}\n{compatibility}\nСостояние: {run_state}\n{language_line}",
                style="Panel.Muted.TLabel",
                wraplength=790,
                justify="left",
            ).grid(row=2, column=0, sticky="w", pady=6)
            ttk.Label(card, text=model.get("notes", ""), style="Panel.Muted.TLabel", wraplength=790, justify="left").grid(row=3, column=0, sticky="w", pady=(0, 5))
            action = ttk.Frame(card, style="Panel.TFrame")
            action.grid(row=4, column=0, sticky="ew")
            if model.get("license_url"):
                ttk.Button(action, text="Лицензия и карточка", command=lambda url=model["license_url"]: webbrowser.open(url)).pack(side="left")
            elif model.get("custom"):
                ttk.Label(action, text="Добавлена с устройства", style="Panel.Muted.TLabel").pack(side="left")
            if model.get("status") == "available":
                self._model_checks[model["id"]] = tk.BooleanVar(value=False)
                if not model.get("custom") and not installed_now:
                    ttk.Checkbutton(action, text=f"Я ознакомился с лицензией {model['license']} и согласен скачать {model['name']} объёмом {_format_size(model['size_bytes'])}", variable=self._model_checks[model["id"]]).pack(side="left", padx=8)
                text = "Проверить модель" if installed_now else "Скачать модель"
                ttk.Button(action, text=text, style="Accent.TButton", command=lambda item=model: self._consent_and_download(item)).pack(side="right")
                if self.active is not None:
                    ttk.Button(
                        action,
                        text="Выбрать для проекта",
                        command=lambda model_id=model["id"]: self._select_model_for_project(model_id),
                    ).pack(side="right", padx=(0, 8))
            else:
                state_text = (
                    "Файл не найден · добавьте GGUF заново"
                    if model.get("custom")
                    else "Загрузка отключена до проверки runtime"
                )
                ttk.Label(action, text=state_text, style="Panel.Muted.TLabel").pack(side="right")
        self.root.after_idle(lambda: canvas.configure(scrollregion=canvas.bbox("all")))

    def _select_model_for_project(self, model_id: str) -> None:
        if self.active is None:
            messagebox.showinfo("Сначала выберите проект", "Откройте проект или создайте новый, чтобы назначить ему модель.", parent=self.root)
            return
        model = next((item for item in self._catalog() if item["id"] == model_id), None)
        if model is None:
            messagebox.showerror("Модель не найдена", "Обновите список моделей и попробуйте ещё раз.", parent=self.root)
            return
        if model.get("status") != "available":
            messagebox.showwarning("Модель недоступна", "Добавьте файл модели заново или выберите другую модель.", parent=self.root)
            return
        unsupported = [
            code
            for code in self.active.target_languages()
            if not supports_language(model, code)
        ]
        source = self.active.project["source_lang"]
        if source != AUTO_LANGUAGE and not supports_language(model, source):
            unsupported.insert(0, source)
        if unsupported:
            labels = ", ".join(language_label(code) for code in dict.fromkeys(unsupported))
            messagebox.showwarning(
                "Языки не указаны для модели",
                f"Для этой модели не настроены языки: {labels}. Измените языки проекта или выберите другую модель.",
                parent=self.root,
            )
            return
        self.active.update_settings(model_id=model_id)
        self.sidebar_status.configure(text=f"Для проекта выбрана модель «{model['name']}»")
        self.show_page("models")

    def _add_custom_model(self) -> None:
        if self._custom_model_worker is not None and self._custom_model_worker.is_alive():
            self.sidebar_status.configure(text="Импорт модели уже выполняется")
            return
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="Выберите локальную GGUF-модель",
            filetypes=[("Модели GGUF", "*.gguf")],
        )
        if not selected:
            return
        path = Path(selected)
        try:
            size = path.stat().st_size
        except OSError as exc:
            messagebox.showerror("Файл недоступен", str(exc), parent=self.root)
            return
        name = simpledialog.askstring("Добавить модель", "Название модели для списка:", initialvalue=path.stem, parent=self.root)
        if not name:
            return
        license_name = simpledialog.askstring(
            "Лицензия модели",
            "Укажите лицензию из карточки модели. Можно оставить пустым, если она неизвестна.",
            initialvalue="",
            parent=self.root,
        )
        if license_name is None:
            return
        default_codes = ["en", "ru"]
        if self.active is not None:
            default_codes = list(self.active.target_languages())
            source = self.active.project["source_lang"]
            if source != AUTO_LANGUAGE:
                default_codes.insert(0, source)
        codes_value = simpledialog.askstring(
            "Языки модели",
            "Перечислите языковые коды, которые хотите видеть в настройках проекта, через запятую. Сверьтесь с карточкой модели. Поддержка и качество не проверяются.",
            initialvalue=",".join(dict.fromkeys(default_codes)),
            parent=self.root,
        )
        if codes_value is None:
            return
        context_value = simpledialog.askstring(
            "Контекст модели",
            "Размер контекста токенов. Используйте значение из карточки модели; по умолчанию 4096.",
            initialvalue="4096",
            parent=self.root,
        )
        if context_value is None:
            return
        try:
            context_size = int(context_value)
        except ValueError:
            messagebox.showerror("Некорректный контекст", "Введите число токенов из списка 2048, 4096, 8192, 16384 или 32768.", parent=self.root)
            return
        if not messagebox.askyesno(
            "Импортировать локальную модель?",
            f"Файл: {path}\nРазмер: {_format_size(size)}\nПапка DotLingo: {self.models_dir}\n\nБудет создана проверенная копия GGUF. Исходный файл останется на месте. Продолжить?",
            parent=self.root,
        ):
            return
        language_codes = [code.strip() for code in codes_value.split(",")]
        self.sidebar_status.configure(text="Копирование и проверка GGUF…")
        self._custom_model_worker = self._submit(
            "custom_model_import",
            lambda: import_custom_model(
                path,
                name,
                language_codes,
                root=self.models_dir,
                context_size=context_size,
                license_name=license_name,
            ),
            self._custom_model_import_done,
        )

    def _custom_model_import_done(self, result: Any) -> None:
        if self._pending_close:
            return
        if isinstance(result, Exception):
            self.sidebar_status.configure(text="Не удалось добавить модель")
            messagebox.showerror("Ошибка импорта модели", str(result), parent=self.root)
            return
        self.sidebar_status.configure(text=f"Добавлена модель «{result['name']}»")
        if self.page == "models":
            self.show_page("models")
        messagebox.showinfo(
            "Модель добавлена",
            f"«{result['name']}» скопирована и проверена по SHA-256. Совместимость runtime и качество перевода не проверены.",
            parent=self.root,
        )

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
        ttk.Label(header, text=f"Загрузка · {model['name']}", style="Panel.Section.TLabel").pack(side="left", anchor="w")
        close_button = ttk.Button(header, text="×", width=3, command=cancel_download)
        close_button.pack(side="right")
        ttk.Label(panel, text="Файл проверяется по размеру и SHA-256 перед активацией.", style="Panel.Muted.TLabel").pack(anchor="w", pady=(12, 4))
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
        self.content.rowconfigure(0, weight=0)
        self.content.rowconfigure(1, weight=1)
        targets = store.target_languages()
        if self._glossary_target_lang not in targets:
            self._glossary_target_lang = targets[0]
        toolbar = ttk.Frame(self.content)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        toolbar.columnconfigure(0, weight=1)
        info = ttk.Label(
            toolbar,
            text=f"Глоссарий проекта · {language_label(store.project['source_lang'])} →",
            style="Muted.TLabel",
        )
        info.grid(row=0, column=0, sticky="w")
        target_var = tk.StringVar(value=language_display(self._glossary_target_lang))
        target_combo = ttk.Combobox(
            toolbar,
            textvariable=target_var,
            state="readonly",
            values=tuple(language_display(code) for code in targets),
            width=22,
        )
        target_combo.grid(row=0, column=1, sticky="e")
        tree = ttk.Treeview(self.content, columns=("target",), show="headings", selectmode="browse")
        tree.heading("#0", text="Исходный термин")
        tree.heading("target", text="Целевой термин")
        tree.column("#0", width=320)
        tree.column("target", width=320)
        tree.grid(row=1, column=0, sticky="nsew")
        for term in store.glossary(self._glossary_target_lang):
            tree.insert("", "end", text=term["source"], values=(term["target"],))

        def change_glossary_language(_event: tk.Event[Any] | None = None) -> None:
            chosen = next(
                (code for code in targets if language_display(code) == target_var.get()),
                self._glossary_target_lang,
            )
            if chosen == self._glossary_target_lang:
                return
            self._glossary_target_lang = chosen
            tree.delete(*tree.get_children())
            for term in store.glossary(chosen):
                tree.insert("", "end", text=term["source"], values=(term["target"],))

        target_combo.bind("<<ComboboxSelected>>", change_glossary_language)
        bottom = ttk.Frame(self.content)
        bottom.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(
            bottom,
            text="Добавить термин",
            style="Accent.TButton",
            command=lambda: self._add_glossary_term(self._glossary_target_lang),
        ).pack(side="left")
        ttk.Label(bottom, text="Совпадения без учёта регистра временно защищаются внутри каждого фрагмента.", style="Muted.TLabel", wraplength=600).pack(side="left", padx=12)

    def _add_glossary_term(self, target_lang: str | None = None) -> None:
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
            store.add_glossary_term(source, target, target_lang=target_lang)
            self.show_page("glossary")
        except ValueError as exc:
            messagebox.showerror("Пустой термин", str(exc), parent=self.root)

    def _show_settings(self) -> None:
        store = self._active_store()
        self._settings_form = None
        panel = self._panel(self.content)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.columnconfigure(1, weight=1)
        panel.rowconfigure(6, weight=1)
        preferences = load_preferences(self.preferences_root)
        reduce_var = tk.BooleanVar(value=bool(preferences["reduce_motion"]))
        ttk.Label(
            panel,
            text="Параметры проекта и устройства",
            style="Panel.TLabel",
            font=(TOKENS["font"], 12, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        if store:
            models = [item for item in self._catalog() if item.get("status") == "available"]
            model_by_name = {item["name"]: item for item in models}
            current_model = store.project.get("model_id") or "qwen3-1.7b-q8"
            initial_model = next(
                (item["name"] for item in models if item["id"] == current_model),
                models[0]["name"],
            )
            model_var = tk.StringVar(value=initial_model)
            ttk.Label(panel, text="Локальная модель", style="Panel.Eyebrow.TLabel").grid(
                row=1, column=0, sticky="w"
            )
            model_combo = ttk.Combobox(
                panel,
                textvariable=model_var,
                state="readonly",
                values=tuple(model_by_name),
            )
            model_combo.grid(row=2, column=0, sticky="ew", padx=(0, 18), pady=(5, 10))

            source_var = tk.StringVar()
            ttk.Label(panel, text="Исходный язык", style="Panel.Eyebrow.TLabel").grid(
                row=3, column=0, sticky="w"
            )
            source_combo = ttk.Combobox(panel, textvariable=source_var, state="readonly")
            source_combo.grid(row=4, column=0, sticky="ew", padx=(0, 18), pady=(5, 10))

            ttk.Label(
                panel,
                text="Целевые языки (Ctrl + Shift: выбор нескольких)",
                style="Panel.Eyebrow.TLabel",
            ).grid(
                row=5, column=0, sticky="w"
            )
            targets_frame = ttk.Frame(panel, style="Panel.TFrame")
            targets_frame.grid(row=6, column=0, rowspan=3, sticky="nsew", padx=(0, 18), pady=(5, 10))
            targets_frame.columnconfigure(0, weight=1)
            targets_frame.rowconfigure(0, weight=1)
            target_list = tk.Listbox(
                targets_frame,
                selectmode="extended",
                exportselection=False,
                height=6,
                bg=TOKENS["surface_raised"],
                fg=TOKENS["text"],
                selectbackground=TOKENS["highlight"],
                selectforeground=TOKENS["text"],
                highlightthickness=1,
                highlightbackground=TOKENS["border"],
                highlightcolor=TOKENS["border_focus"],
                relief="flat",
                activestyle="none",
                font=(TOKENS["font"], TOKENS["font_body"]),
            )
            target_list.grid(row=0, column=0, sticky="nsew")
            target_scroll = ttk.Scrollbar(targets_frame, orient="vertical", command=target_list.yview)
            target_scroll.grid(row=0, column=1, sticky="ns")
            target_list.configure(yscrollcommand=target_scroll.set)
            language_codes: list[str] = []

            def refresh_language_choices(_event: tk.Event[Any] | None = None) -> None:
                previous_targets = {
                    language_codes[index] for index in target_list.curselection()
                    if index < len(language_codes)
                }
                current_source = source_var.get()
                selected_model = model_by_name.get(model_var.get())
                codes = list(supported_languages(selected_model or {}))
                language_codes[:] = codes
                source_choices = [AUTO_LANGUAGE_LABEL, *(language_display(code) for code in codes)]
                source_combo.configure(values=source_choices)
                source_by_code = store.project["source_lang"]
                desired_source = (
                    AUTO_LANGUAGE_LABEL
                    if source_by_code == AUTO_LANGUAGE
                    else language_display(source_by_code)
                )
                if current_source in source_choices:
                    desired_source = current_source
                if desired_source not in source_choices:
                    desired_source = AUTO_LANGUAGE_LABEL
                source_var.set(desired_source)
                source_code = (
                    AUTO_LANGUAGE
                    if desired_source == AUTO_LANGUAGE_LABEL
                    else next(
                        (code for code in codes if language_display(code) == desired_source),
                        AUTO_LANGUAGE,
                    )
                )
                target_list.delete(0, "end")
                for index, code in enumerate(codes):
                    target_list.insert("end", language_display(code))
                    if code != source_code and (code in previous_targets or (
                        not previous_targets and code in store.target_languages()
                    )):
                        target_list.selection_set(index)
                if not target_list.curselection() and codes:
                    target_list.selection_set(next(
                        (index for index, code in enumerate(codes) if code != source_code),
                        0,
                    ))

            model_combo.bind("<<ComboboxSelected>>", refresh_language_choices)
            source_combo.bind("<<ComboboxSelected>>", refresh_language_choices)
            refresh_language_choices()

            ttk.Label(panel, text="Контекст проекта", style="Panel.Eyebrow.TLabel").grid(
                row=1, column=1, sticky="w"
            )
            context = self._text_widget(panel, height=5)
            context.grid(row=2, column=1, sticky="nsew", pady=(5, 10))
            context.insert("1.0", store.project.get("context", ""))
            ttk.Label(panel, text="Правила перевода", style="Panel.Eyebrow.TLabel").grid(
                row=3, column=1, sticky="w"
            )
            rules = self._text_widget(panel, height=6)
            rules.grid(row=4, column=1, rowspan=5, sticky="nsew", pady=(5, 10))
            rules.insert("1.0", store.project.get("rules", ""))
            initial = self._settings_form_values_from_widgets(
                model_var, source_var, target_list, language_codes, context, rules, model_by_name
            )
            self._settings_form = (
                store,
                model_var,
                source_var,
                target_list,
                language_codes,
                context,
                rules,
                model_by_name,
                initial,
            )

            def save_project_settings() -> None:
                self._persist_settings_form()

            ttk.Button(
                panel,
                text="Сохранить настройки проекта",
                style="Accent.TButton",
                command=save_project_settings,
            ).grid(row=9, column=0, columnspan=2, sticky="e", pady=(0, 8))
        hardware_label = self._hardware_text()
        self._settings_hardware = ttk.Label(
            panel, text=hardware_label, style="Panel.Muted.TLabel", justify="left", wraplength=720
        )
        self._settings_hardware.grid(row=10, column=0, columnspan=2, sticky="w", pady=(10, 7))
        ttk.Button(panel, text="Проверить устройство", command=self._detect_hardware).grid(
            row=11, column=0, sticky="w"
        )
        ttk.Checkbutton(
            panel,
            text="Уменьшить движение и переходы интерфейса",
            variable=reduce_var,
            command=lambda: self._save_motion(reduce_var.get()),
        ).grid(row=11, column=1, sticky="e", pady=(0, 0))
        ttk.Label(
            panel,
            text=f"Данные проекта: {self.projects_dir}\nВеса моделей: {self.models_dir}",
            style="Panel.Muted.TLabel",
            wraplength=700,
        ).grid(row=12, column=0, columnspan=2, sticky="w", pady=(12, 0))

    @staticmethod
    def _settings_form_values_from_widgets(
        model_var: tk.StringVar,
        source_var: tk.StringVar,
        target_list: tk.Listbox,
        language_codes: list[str],
        context: tk.Text,
        rules: tk.Text,
        model_by_name: dict[str, dict[str, Any]],
    ) -> tuple[str, str, tuple[str, ...], str, str]:
        model = model_by_name.get(model_var.get())
        source = (
            AUTO_LANGUAGE
            if source_var.get() == AUTO_LANGUAGE_LABEL
            else next(
                (code for code in language_codes if language_display(code) == source_var.get()),
                AUTO_LANGUAGE,
            )
        )
        targets = tuple(
            language_codes[index]
            for index in target_list.curselection()
            if index < len(language_codes)
        )
        return (
            model["id"] if model else "",
            source,
            targets,
            context.get("1.0", "end-1c"),
            rules.get("1.0", "end-1c"),
        )

    def _settings_form_values(self) -> tuple[str, str, tuple[str, ...], str, str] | None:
        form = self._settings_form
        if form is None:
            return None
        _, model_var, source_var, target_list, language_codes, context, rules, model_by_name, _ = form
        return self._settings_form_values_from_widgets(
            model_var, source_var, target_list, language_codes, context, rules, model_by_name
        )

    def _persist_settings_form(self) -> bool:
        form = self._settings_form
        values = self._settings_form_values()
        if form is None or values is None:
            return True
        store = form[0]
        if not values[2]:
            self.sidebar_status.configure(text="Выберите хотя бы один целевой язык")
            return False
        if values[1] != AUTO_LANGUAGE and values[1] in values[2]:
            self.sidebar_status.configure(text="Исходный язык не может совпадать с целевым")
            return False
        try:
            store.update_settings(
                model_id=values[0],
                source_lang=values[1],
                target_langs=list(values[2]),
                context=values[3],
                rules=values[4],
            )
        except Exception as exc:
            self.sidebar_status.configure(text=f"Не удалось сохранить настройки: {exc}")
            return False
        self._settings_form = (*form[:-1], values)
        self.sidebar_status.configure(text="Настройки проекта сохранены")
        return True

    def _confirm_settings_change(self) -> bool:
        form = self._settings_form
        values = self._settings_form_values()
        if form is None or values is None or values == form[-1]:
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
        if value:
            self._stop_nav_animation()
            self._stop_page_animation()
            self._title_rule.configure(width=68)
            for page, indicator in self.nav_indicators.items():
                indicator.configure(
                    background=TOKENS["accent"] if page == self.page else TOKENS["sidebar"]
                )
                icon = self.nav_icons[page]
                self._draw_nav_icon(
                    icon,
                    page,
                    TOKENS["text"] if page == self.page else TOKENS["muted"],
                )
        for progress in (
            getattr(self, "_wizard_progress", (None,))[0],
            (getattr(self, "_download_progress_widgets", None) or (None, None))[1],
        ):
            if progress is not None and progress.winfo_exists() and progress.cget("mode") == "indeterminate":
                if value:
                    progress.stop()
                    progress.configure(mode="determinate", value=0)
                else:
                    progress.start(12)

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
        threads = (
            f"{max(1, min(8, item.cpu_threads - 1))} потоков inference (автонастройка)"
            if item.cpu_threads
            else "число потоков не удалось определить"
        )
        recommendation, reason = self._recommended_model()
        recommendation_text = (
            f"Рекомендация по ресурсам: {recommendation['name']} · {reason}"
            if recommendation
            else f"Рекомендация: {reason}"
        )
        return (
            f"Профиль: {item.profile}\nCPU: {threads}\nRAM: {ram}\nСвободный диск: {disk}\n"
            f"GPU: {gpu}\nllama.cpp: {runtime}; GPU offload в этом backend: {gpu_runtime}.\n"
            f"{recommendation_text}"
        )

    def _detect_hardware(self) -> None:
        if self._device_check_in_progress:
            return
        self._device_check_in_progress = True
        self.sidebar_status.configure(text="Проверка устройства…")
        self._submit("hardware", lambda: detect(self.models_dir), self._hardware_done)

    def _hardware_done(self, result: Any) -> None:
        self._device_check_in_progress = False
        if isinstance(result, Exception):
            self.sidebar_status.configure(text=f"Не удалось определить устройство: {result}")
            return
        self.hardware = result
        model, reason = self._sync_recommended_model()
        self.sidebar_status.configure(text=f"Подбор: {model['name']}" if model else f"Устройство: {result.profile}")
        if hasattr(self, "_settings_hardware") and self._settings_hardware.winfo_exists():
            self._settings_hardware.configure(text=f"{self._hardware_text()}\n\nРекомендация: {reason}")
        if self.page == "models":
            self.show_page("models")

    def open_setup_wizard(self) -> None:
        if self._overlay is not None and self._overlay.winfo_exists():
            return
        window, panel = self._open_overlay()
        state: dict[str, Any] = {
            "step": 0,
            "selected": None,
            "cancel": None,
            "hardware_started": False,
            "closed": False,
        }
        panel.columnconfigure(0, weight=1)
        header = ttk.Frame(panel, style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Подготовка локального перевода", style="Panel.Section.TLabel").pack(side="left", anchor="w")
        ttk.Button(header, text="Отложить", command=lambda: finish()).pack(side="right")
        step_label = ttk.Label(panel, text="", style="Panel.Muted.TLabel")
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
                recommended, _reason = self._sync_recommended_model()
                if recommended is not None:
                    state["selected"] = recommended["id"]
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
                details = ttk.Label(body, text="Проверка выполняется локально. Если значение получить нельзя, оно останется неизвестным.", style="Panel.Muted.TLabel", wraplength=680, justify="left")
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
                recommended, reason = self._recommended_model()
                selected = next(
                    (item for item in self._catalog() if item["id"] == state["selected"]),
                    recommended or self._catalog()[0],
                )
                estimate = f"Подходящий вариант: {selected['name']} · {_format_size(selected.get('size_bytes'))}."
                ttk.Label(body, text=estimate, style="Panel.TLabel", wraplength=680, justify="left").pack(anchor="w", pady=8)
                ttk.Label(body, text=reason, style="Panel.Muted.TLabel", wraplength=680, justify="left").pack(anchor="w", pady=4)
                ttk.Label(body, text=self._hardware_text(), style="Panel.Muted.TLabel", wraplength=680, justify="left").pack(anchor="w", pady=8)
                ttk.Label(body, text="Установка модели необязательна. Можно отложить её и выбрать модель позже в разделе «Модели».", style="Panel.Muted.TLabel", wraplength=680, justify="left").pack(anchor="w", pady=8)
                ttk.Button(buttons, text="Назад", command=lambda: render(0)).pack(side="left")
                focus_target = ttk.Button(buttons, text="Выбрать модель", style="Accent.TButton", command=lambda: render(2))
                focus_target.pack(side="right")
            elif step == 2:
                model_var = tk.StringVar(value=state["selected"])
                for item in self._catalog():
                    row = ttk.Frame(body, style="Panel.TFrame")
                    row.pack(fill="x", pady=3)
                    available = item.get("status") == "available" and not item.get("custom")
                    ttk.Radiobutton(
                        row,
                        text=f"{item['name']} · {_format_size(item.get('size_bytes'))}",
                        value=item["id"],
                        variable=model_var,
                        state="normal" if available else "disabled",
                    ).pack(side="left")
                    if item.get("license_url"):
                        ttk.Button(row, text="Карточка и лицензия", command=lambda url=item["license_url"]: webbrowser.open(url)).pack(side="right")
                model_var.trace_add("write", lambda *_args: state.update(selected=model_var.get()))
                consent_var = tk.BooleanVar(value=False)
                consent = ttk.Checkbutton(body, text="Я ознакомился с лицензией выбранной модели и соглашаюсь скачать её вес.", variable=consent_var, wraplength=680)
                consent.pack(anchor="w", pady=(12, 4))
                ttk.Label(body, text="Лицензии различаются. Размер, версия и SHA-256 закреплены в карточке модели. Неизвестная совместимость не считается подтверждённой.", style="Panel.Muted.TLabel", wraplength=680, justify="left").pack(anchor="w", pady=5)

                def start_download() -> None:
                    item = next(entry for entry in self._catalog() if entry["id"] == model_var.get())
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
                progress = ttk.Progressbar(
                    body, mode="determinate" if self.reduce_motion else "indeterminate"
                )
                progress.pack(fill="x", pady=10)
                if not self.reduce_motion:
                    progress.start(12)
                status = ttk.Label(body, text="Ожидание ответа сервера · общий прогресс и ETA могут быть неизвестны", style="Panel.Muted.TLabel", wraplength=680)
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
                ttk.Label(body, text=runtime_note, style="Panel.Muted.TLabel", wraplength=680, justify="left").pack(anchor="w", pady=8)
                ttk.Label(body, text="OCR для сканированных PDF не устанавливается автоматически.", style="Panel.Muted.TLabel", wraplength=680, justify="left").pack(anchor="w", pady=8)
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
        empty = self._panel(self.content, padding=24)
        empty.grid(row=0, column=0, sticky="nsew")
        empty.columnconfigure(0, weight=1)
        empty.rowconfigure(0, weight=1)
        message = ttk.Frame(empty, style="Panel.TFrame")
        message.grid(row=0, column=0)
        ttk.Label(message, text="Сначала выберите проект", style="Panel.Section.TLabel").pack()
        ttk.Label(
            message,
            text="Создайте новый или откройте сохранённый проект.",
            style="Panel.Muted.TLabel",
        ).pack(pady=(6, 14))
        actions = ttk.Frame(message, style="Panel.TFrame")
        actions.pack()
        ttk.Button(
            actions,
            text="Выбрать проект",
            command=lambda: self.show_page("projects"),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            actions,
            text="Новый перевод",
            style="Accent.TButton",
            command=self._new_project,
        ).pack(side="left")

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
                if not self.reduce_motion:
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
                    if not self.reduce_motion:
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
        workers = (self._download_worker, getattr(self, "_custom_model_worker", None))
        if any(worker is not None and worker.is_alive() for worker in workers):
            self._pending_close = True
            self._close_cancel_accepted = self._download_cancel.set() if self._download_cancel else None
            self._show_close_wait_status()
            self._close_wait_job = self.root.after(100, self._wait_for_download_close)
            return
        self._finalize_close()

    def _show_close_wait_status(self) -> None:
        custom_worker = getattr(self, "_custom_model_worker", None)
        custom_import = custom_worker is not None and custom_worker.is_alive()
        if custom_import:
            message = "Импорт модели завершится перед закрытием приложения…"
        elif self._close_cancel_accepted is True:
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
        workers = (self._download_worker, getattr(self, "_custom_model_worker", None))
        if any(worker is not None and worker.is_alive() for worker in workers):
            self._show_close_wait_status()
            self._close_wait_job = self.root.after(100, self._wait_for_download_close)
            return
        self._download_worker = None
        self._custom_model_worker = None
        self._pending_close = False
        self._finalize_close()

    def _finalize_close(self) -> None:
        self._closing = True
        self._stop_nav_animation()
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
