"""Установка закреплённых колёс llama.cpp. Только по явному действию, без оболочки shell.

Колесо скачивается по фиксированному адресу, SHA-256 сверяется до pip, а переменные PIP_*
в этот запуск не попадают. Setup.exe вызывает тот же путь для CPU и сам CUDA не скачивает.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotlingo.hardware import HardwareSnapshot, has_discrete_nvidia

# Готовые CUDA-колёса опубликованы не под каждую версию Python.
CUDA_PYTHON = ((3, 10), (3, 11), (3, 12))

_LLAMA_FILENAME = "llama_cpp_python-0.3.35-py3-none-win_amd64.whl"
_CPU_WHEEL_URL = (
    "https://github.com/abetlen/llama-cpp-python/releases/download/"
    "v0.3.35/llama_cpp_python-0.3.35-py3-none-win_amd64.whl"
)
_CUDA_WHEEL_URL = (
    "https://github.com/abetlen/llama-cpp-python/releases/download/"
    "v0.3.35-cu124/llama_cpp_python-0.3.35-py3-none-win_amd64.whl"
)
_CPU_SHA256 = "31590ea000d5aff6f05f1e428048e72318a83709288159a5bd4dabec530080bb"
_CUDA_SHA256 = "84f7218c1e9cf21014b9770c65064c5a3674dd2f4fbc312c5d6bb40cec0fb269"
_CPU_SIZE = 7_086_788
_CUDA_SIZE = 482_736_710

# Версии cu12, опубликованные на PyPI для линейки CUDA 12. Хэши взяты из JSON PyPI.
_NVIDIA_WHEELS = (
    {
        "name": "nvidia-cublas-cu12",
        "filename": "nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl",
        "url": (
            "https://files.pythonhosted.org/packages/20/e2/"
            "fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/"
            "nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl"
        ),
        "sha256": "623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661",
        "size": 553_162_896,
    },
    {
        "name": "nvidia-cuda-runtime-cu12",
        "filename": "nvidia_cuda_runtime_cu12-12.9.79-py3-none-win_amd64.whl",
        "url": (
            "https://files.pythonhosted.org/packages/59/df/"
            "e7c3a360be4f7b93cee39271b792669baeb3846c58a4df6dfcf187a7ffab/"
            "nvidia_cuda_runtime_cu12-12.9.79-py3-none-win_amd64.whl"
        ),
        "sha256": "8e018af8fa02363876860388bd10ccb89eb9ab8fb0aa749aaf58430a9f7c4891",
        "size": 3_591_604,
    },
)


class RuntimeInstallError(RuntimeError):
    """Установка сборки не выполнена. Текст можно показать пользователю."""


def packaged_app() -> bool:
    """Собранный PyInstaller. Внутри него pip не меняет уже вшитый runtime."""
    return bool(getattr(sys, "frozen", False))


def cuda_wheel_supported(version: tuple[int, int] | None = None) -> bool:
    current = version if version is not None else sys.version_info[:2]
    return current in CUDA_PYTHON


def _trusted_wheel_https(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if host in {"github.com", "files.pythonhosted.org", "pypi.org"}:
        return True
    return host.endswith(".githubusercontent.com") or host.endswith(".pypi.org")


class _WheelRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        if not _trusted_wheel_https(newurl):
            raise RuntimeInstallError("Перенаправление колеса ведёт на неизвестный домен.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_wheel_request(request: urllib.request.Request, timeout: float) -> Any:
    opener = urllib.request.build_opener(_WheelRedirect)
    return opener.open(request, timeout=timeout)


def _llama_pin(url: str, digest: str, size: int) -> dict[str, Any]:
    return {
        "name": "llama-cpp-python",
        "filename": _LLAMA_FILENAME,
        "url": url,
        "sha256": digest,
        "size": size,
        "label": "llama.cpp",
        "force": True,
    }


def wheel_pins(accelerator_id: str) -> list[dict[str, Any]]:
    """Закреплённые колёса по порядку. Пустой список значит неизвестную сборку."""
    if accelerator_id == "cpu":
        return [_llama_pin(_CPU_WHEEL_URL, _CPU_SHA256, _CPU_SIZE)]
    if accelerator_id == "cuda":
        runtime = [
            {**item, "label": "библиотеки CUDA", "force": False} for item in _NVIDIA_WHEELS
        ]
        runtime.append(_llama_pin(_CUDA_WHEEL_URL, _CUDA_SHA256, _CUDA_SIZE))
        return runtime
    return []


def _pip_local(wheel: Path, *, force: bool) -> list[str]:
    command = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    if force:
        command.extend(["--upgrade", "--force-reinstall"])
    command.append(str(wheel))
    return command


def _pip_env() -> dict[str, str]:
    """Окружение pip без PIP_INDEX_URL, PIP_EXTRA_INDEX_URL, PIP_TRUSTED_HOST и остальных PIP_*."""
    return {key: value for key, value in os.environ.items() if not key.upper().startswith("PIP_")}


def _download_verified(pin: dict[str, Any], destination: Path) -> None:
    """Скачать колесо и сверить размер с SHA-256. При ошибке файл не остаётся."""
    url = str(pin["url"])
    if not _trusted_wheel_https(url):
        raise RuntimeInstallError("Источник колеса отсутствует в списке доверенных HTTPS-доменов.")
    request = urllib.request.Request(url, headers={"User-Agent": "DotLingo/0.1 runtime"})
    digest = hashlib.sha256()
    size = 0
    expected = int(pin["size"])
    try:
        with _open_wheel_request(request, timeout=120) as response:
            if not _trusted_wheel_https(response.geturl()):
                raise RuntimeInstallError("Перенаправление колеса ведёт на неизвестный домен.")
            with destination.open("wb") as stream:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > expected:
                        raise RuntimeInstallError("Колесо больше закреплённого размера.")
                    digest.update(chunk)
                    stream.write(chunk)
        if size != expected or digest.hexdigest() != str(pin["sha256"]):
            raise RuntimeInstallError("Контрольная сумма колеса не совпала. Файл не установлен.")
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def runtime_choices(snapshot: HardwareSnapshot | None) -> dict[str, object]:
    """Какие сборки можно поставить на это железо и этот Python."""
    frozen = packaged_app()
    python = f"{sys.version_info.major}.{sys.version_info.minor}"
    nvidia = has_discrete_nvidia(snapshot)
    cuda_python = cuda_wheel_supported()
    if frozen:
        cuda_reason = "В установленной программе колесо уже внутри сборки."
        cpu_reason = cuda_reason
        cuda_ok = False
        cpu_ok = False
    elif not nvidia:
        cuda_reason = "Нет дискретной видеокарты NVIDIA."
        cpu_reason = "Работает на процессоре."
        cuda_ok = False
        cpu_ok = True
    elif not cuda_python:
        cuda_reason = f"Готовой сборки CUDA под Python {python} нет."
        cpu_reason = "Работает на процессоре."
        cuda_ok = False
        cpu_ok = True
    else:
        cuda_reason = "Видеокарта NVIDIA. Сборка скачает библиотеки CUDA 12 и заменит колесо."
        cpu_reason = "Работает на процессоре."
        cuda_ok = True
        cpu_ok = True
    choices = [
        {
            "id": "cuda",
            "label": "CUDA",
            "detail": cuda_reason,
            "available": cuda_ok,
            "reason": cuda_reason,
        },
        {
            "id": "cpu",
            "label": "Процессор",
            "detail": cpu_reason,
            "available": cpu_ok,
            "reason": cpu_reason,
        },
    ]
    recommended = next((item["id"] for item in choices if item["available"]), None)
    for item in choices:
        item["recommended"] = item["id"] == recommended
    return {"frozen": frozen, "recommended": recommended, "choices": choices}


def _failure_message(label: str, output: str) -> str:
    text = output.casefold()
    if "no matching distribution" in text or "could not find a version" in text:
        return f"Для этого Python нет готовой сборки «{label}»."
    if "permission denied" in text or "access is denied" in text or "winerror 5" in text:
        return f"Не удалось записать {label}: нет прав на каталог Python."
    if any(word in text for word in ("timed out", "connection", "name resolution", "network")):
        return f"Не удалось скачать {label}. Проверьте сеть и повторите."
    return f"Не удалось поставить {label}."


def run_install(
    accelerator_id: str,
    on_progress: Callable[[dict[str, object]], None] | None = None,
) -> None:
    """Поставить выбранную сборку. Первая ошибка останавливает цепочку.

    Зависимости локального колеса берутся с PyPI по умолчанию. Чужой индекс и
    --no-index здесь не используются: без зависимостей колесо не импортируется.
    """
    if packaged_app():
        raise RuntimeInstallError(
            "В установленной программе runtime уже внутри сборки. "
            "Сменить колесо можно только в исходном запуске."
        )
    pins = wheel_pins(accelerator_id)
    if not pins:
        raise RuntimeInstallError("Неизвестная сборка runtime.")
    total = len(pins)
    with tempfile.TemporaryDirectory(prefix="dotlingo-wheels-") as temp_name:
        folder = Path(temp_name)
        for index, pin in enumerate(pins, start=1):
            label = str(pin["label"])
            if on_progress is not None:
                on_progress(
                    {
                        "phase": label,
                        "step": index,
                        "steps": total,
                        "indeterminate": True,
                        "acceleratorId": accelerator_id,
                    }
                )
            destination = folder / str(pin["filename"])
            try:
                _download_verified(pin, destination)
            except RuntimeInstallError:
                raise
            except (OSError, urllib.error.URLError, TimeoutError) as exc:
                raise RuntimeInstallError(
                    f"Не удалось скачать {label}. Проверьте сеть и повторите."
                ) from exc
            command = _pip_local(destination, force=bool(pin["force"]))
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                    env=_pip_env(),
                )
            except OSError as exc:
                raise RuntimeInstallError(f"Не удалось запустить pip для {label}.") from exc
            if completed.returncode != 0:
                output = f"{completed.stderr or ''}\n{completed.stdout or ''}"
                raise RuntimeInstallError(_failure_message(label, output))
