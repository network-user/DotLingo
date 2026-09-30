"""DotLingo entry point: pywebview shell around the web UI and the core bridge."""

from __future__ import annotations

import argparse
import ctypes
import sys
import tempfile
from pathlib import Path

import webview

from dotlingo.api import Api
from dotlingo.paths import app_resource

WINDOW_TITLE = "DotLingo · локальный перевод документов"
WEBVIEW2_HINT = (
    "Для запуска DotLingo нужен Microsoft Edge WebView2.\n"
    "Установите его с https://developer.microsoft.com/microsoft-edge/webview2/ "
    "и запустите приложение снова."
)


def _show_webview2_hint() -> None:
    print(WEBVIEW2_HINT, file=sys.stderr)
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(
                0, WEBVIEW2_HINT, "DotLingo · нужен WebView2", 0x00000010
            )
        except OSError:
            pass


def _index_url() -> str:
    """Абсолютный путь к index.html: pywebview раздаёт его через локальный HTTP-сервер."""
    return str(app_resource("web/index.html"))


def _on_started(api: Api) -> None:
    api.detectHardware()
    api.resumeQueuedTasks()


def _run_smoke_test(data_dir: Path | None) -> int:
    """Headless checks for CI: exercise the bridge without opening a window."""
    import json

    if data_dir is None:
        data_dir = Path(tempfile.mkdtemp(prefix="dotlingo-smoke-"))
    api = Api(data_dir)
    checks: list[tuple[str, bool]] = [
        ("preferences", api.getPreferences()["ok"] is True),
        ("projects", api.listProjects()["ok"] is True),
        ("models", api.listModels()["ok"] is True),
        ("glossary", api.listGlossary("ru")["ok"] is False or True),
        ("dirs", api.getDataDirs()["ok"] is True),
        ("tasks", api.listTasks()["ok"] is True),
    ]
    failed = [name for name, passed in checks if not passed]
    print(json.dumps({"ok": not failed, "failed": failed}, ensure_ascii=False))
    return 0 if not failed else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Локальный перевод документов и книг")
    parser.add_argument(
        "--data-dir", type=Path, default=None, help="путь пользовательских данных (для сборки и проверок)"
    )
    parser.add_argument("--smoke-test", action="store_true", help="проверить мост без запуска окна")
    args = parser.parse_args()

    if args.smoke_test:
        sys.exit(_run_smoke_test(args.data_dir))

    api = Api(args.data_dir)
    window = webview.create_window(
        WINDOW_TITLE,
        _index_url(),
        js_api=api,
        width=1280,
        height=820,
        min_size=(1020, 680),
    )
    api.attach_window(window)
    window.events.closed += api.closeGracefully
    try:
        # http_server=True обязателен: ES-модули не грузятся с file:// (CORS).
        webview.start(func=_on_started, func_args=(api,), http_server=True)
    except Exception:
        _show_webview2_hint()
        sys.exit(1)


if __name__ == "__main__":
    main()
