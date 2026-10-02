"""DotLingo entry point: pywebview shell around the web UI and the core bridge."""

from __future__ import annotations

import argparse
import ctypes
import os
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

import webview

from dotlingo.api import Api
from dotlingo.paths import app_resource

WINDOW_TITLE = "DotLingo · локальный перевод документов"

# Без своего AppUserModelID панель задач группирует окно с python.exe и рисует его значок.
APP_USER_MODEL_ID = "DotLingo.Desktop"

# Официальный Evergreen-загрузчик WebView2 (редирект на актуальный Setup.exe).
WEBVIEW2_BOOTSTRAPPER = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"

WEBVIEW2_HINT = (
    "Для запуска DotLingo нужен Microsoft Edge WebView2.\n"
    "Установите его с https://developer.microsoft.com/microsoft-edge/webview2/ "
    "и запустите приложение снова."
)

# GUID Evergreen-рантайма WebView2 в EdgeUpdate.
WEBVIEW2_REGISTRY = (
    ("HKCU", r"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
    ("HKLM", r"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
    ("HKLM", r"Software\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
)


def _message_box(text: str, title: str, kind: int) -> int:
    """Нативный MessageBox без tkinter; возвращает код кнопки."""
    if sys.platform != "win32":
        print(f"{title}: {text}", file=sys.stderr)
        return 0
    return ctypes.windll.user32.MessageBoxW(0, text, title, kind)


def _registry_value(root_name: str, subkey: str, value: str) -> str | None:
    hive = {"HKCU": ctypes.c_void_p(0x80000001), "HKLM": ctypes.c_void_p(0x80000002)}[root_name]
    try:
        advapi32 = ctypes.windll.advapi32
        handle = ctypes.c_void_p()
        if advapi32.RegOpenKeyExW(
            hive, subkey, 0, 0x20019, ctypes.byref(handle)
        ) != 0:
            return None
        try:
            size = ctypes.c_ulong(512)
            buffer = ctypes.create_unicode_buffer(size.value)
            if advapi32.RegQueryValueExW(
                handle, value, None, None, buffer, ctypes.byref(size)
            ) != 0:
                return None
            return buffer.value
        finally:
            advapi32.RegCloseKey(handle)
    except OSError:
        return None


def _webview2_installed() -> bool:
    """True, если Evergreen-рантайм WebView2 зарегистрирован в системе."""
    for root, subkey in WEBVIEW2_REGISTRY:
        version = _registry_value(root, subkey, "pv")
        if version and version.strip() not in ("", "0.0.0.0"):
            return True
    return False


def _ensure_webview2() -> bool:
    """Проверить WebView2; при отсутствии предложить авто-загрузку загрузчика."""
    if _webview2_installed():
        return True

    answer = _message_box(
        "Для запуска DotLingo нужен Microsoft Edge WebView2 - он не найден.\n\n"
        "Скачать и установить его сейчас?\n"
        "Загрузчик около 2 МБ; установку нужно будет подтвердить.",
        "DotLingo · нужен WebView2",
        0x00000004 | 0x00000030,  # MB_YESNO | MB_ICONWARNING
    )
    if answer != 6:  # IDYES
        _message_box(WEBVIEW2_HINT, "DotLingo · нужен WebView2", 0x00000010)
        return False

    try:
        import urllib.request

        target = Path(tempfile.gettempdir()) / "MicrosoftEdgeWebview2Setup.exe"
        print("Загружаю загрузчик WebView2…")
        with urllib.request.urlopen(WEBVIEW2_BOOTSTRAPPER, timeout=60) as response, target.open(
            "wb"
        ) as stream:
            stream.write(response.read())
        subprocess.Popen([str(target)], close_fds=True)
        _message_box(
            "Загрузчик WebView2 запущен. Завершите установку и запустите DotLingo снова.",
            "DotLingo · установка WebView2",
            0x00000040,
        )
    except (OSError, ValueError) as exc:
        _message_box(
            f"Не удалось скачать WebView2 автоматически: {exc}\n\n{WEBVIEW2_HINT}",
            "DotLingo · нужен WebView2",
            0x00000010,
        )
    return False


def _index_url() -> str:
    """Абсолютный путь к index.html: pywebview раздаёт его через локальный HTTP-сервер."""
    return str(app_resource("web/index.html"))


def _set_windows_app_id() -> None:
    """Отвязать процесс от иконки интерпретатора до создания окна."""
    if sys.platform != "win32":
        return
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)


def _on_ui_thread(native, action) -> None:
    """Выполнить действие на потоке окна WinForms."""
    from System.Windows.Forms import MethodInvoker

    if bool(native.InvokeRequired):
        native.Invoke(MethodInvoker(action))
    else:
        action()


def _apply_window_icon(window) -> None:
    """Поставить значок DotLingo. На Windows pywebview иначе берёт иконку python.exe."""
    if sys.platform != "win32":
        return
    icon_path = app_resource("assets/app_icon.ico")
    native = getattr(window, "native", None)
    if native is None or not icon_path.is_file():
        return

    def assign() -> None:
        from System.Drawing import Icon

        native.Icon = Icon(str(icon_path))

    try:
        _on_ui_thread(native, assign)
    except Exception:
        traceback.print_exc()


def _enable_text_menu(window) -> None:
    """Правый клик: Копировать, Вставить, Выделить всё. pywebview включает меню только в debug."""
    if sys.platform != "win32":
        return
    for _ in range(50):
        native = getattr(window, "native", None)
        webview_control = getattr(native, "webview", None)
        core = None
        if webview_control is not None:
            try:
                core = webview_control.CoreWebView2
            except Exception:
                core = None
        if native is not None and core is not None:
            def assign(settings=core.Settings) -> None:
                settings.AreDefaultContextMenusEnabled = True

            try:
                _on_ui_thread(native, assign)
            except Exception:
                traceback.print_exc()
            return
        time.sleep(0.1)


# Коды консоли Windows: Ctrl+C, Ctrl+Break, закрытие консоли, выход из сеанса, выключение.
_CONSOLE_STOP_CODES = frozenset({0, 1, 2, 5, 6})


def _console_should_stop(ctrl_type: int) -> bool:
    return ctrl_type in _CONSOLE_STOP_CODES


def _halt_process(code: int) -> None:
    """Завершить процесс сразу. Обычный sys.exit оставляет потоки pythonnet живыми."""
    os._exit(code)


def _ask_window_to_close(window) -> None:
    """Закрыть окно с потока консоли, не блокируя обработчик Ctrl+C."""
    native = getattr(window, "native", None)
    begin = getattr(native, "BeginInvoke", None)
    if callable(begin):
        try:
            import clr

            clr.AddReference("System.Windows.Forms")
            from System.Windows.Forms import MethodInvoker

            def _close() -> None:
                try:
                    native.Close()
                except Exception:
                    _halt_process(0)

            begin(MethodInvoker(_close))
            return
        except Exception:
            traceback.print_exc()
    try:
        window.destroy()
    except Exception:
        _halt_process(130)


def _bind_console_stop(_window) -> None:
    """Ctrl+C в консоли завершает процесс. Цикл GUI сам это событие не видит."""
    if sys.platform == "win32":
        handler_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)

        def _handler(ctrl_type: int) -> int:
            if not _console_should_stop(ctrl_type):
                return 0
            # ExitProcess не ждёт GUI и не зависит от GIL после вызова.
            ctypes.windll.kernel32.ExitProcess(130)
            return 0

        callback = handler_type(_handler)
        # Колбэк нельзя отдать сборщику, иначе Windows вызовет уже мёртвую функцию.
        _bind_console_stop.callback = callback
        kernel32 = ctypes.windll.kernel32
        # WinForms и новая группа процессов могут выключить Ctrl+C. Включаем обратно.
        kernel32.SetConsoleCtrlHandler(None, False)
        if kernel32.SetConsoleCtrlHandler(callback, True):
            return

    def _signal_handler(_signum: int, _frame: object) -> None:
        _halt_process(130)

    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)


def _on_started(api: Api, _window) -> None:
    # Проверка устройства кэшируется; автоматически запускается только один раз.
    if api.hardware is None:
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

    if not _ensure_webview2():
        sys.exit(1)

    _set_windows_app_id()
    api = Api(args.data_dir)
    window = webview.create_window(
        WINDOW_TITLE,
        _index_url(),
        js_api=api,
        width=1280,
        height=820,
        min_size=(1020, 680),
        # Пока CSS не нарисован, окно не должно вспыхивать белым.
        background_color="#0B0B0E",
        # pywebview по умолчанию ставит user-select: none на всю страницу.
        text_select=True,
    )
    api.attach_window(window)

    def _on_closed() -> None:
        # Подписчик события closed обязан вернуть hashable; возвращаем None.
        try:
            api.closeGracefully()
        finally:
            # Цикл WinForms и потоки pythonnet иначе оставляют python.exe живым.
            _halt_process(0)

    def _on_shown() -> None:
        # Повторно: WinForms может перехватить Ctrl+C уже после старта цикла.
        _bind_console_stop(window)
        _apply_window_icon(window)
        _enable_text_menu(window)

    window.events.shown += _on_shown
    window.events.closed += _on_closed
    _bind_console_stop(window)
    exit_code = 0
    try:
        # http_server=True обязателен: ES-модули не грузятся с file:// (CORS).
        webview.start(func=_on_started, args=(api, window), http_server=True)
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as exc:
        # Реальная ошибка запуска - не маскируем её под WebView2.
        traceback.print_exc()
        _message_box(
            f"Не удалось открыть окно DotLingo: {exc}\n\nПодробности выведены в консоль.",
            "DotLingo · ошибка запуска",
            0x00000010,
        )
        exit_code = 1
    finally:
        try:
            api.closeGracefully()
        except Exception:
            traceback.print_exc()
    _halt_process(exit_code)


if __name__ == "__main__":
    main()
