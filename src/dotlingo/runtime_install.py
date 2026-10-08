"""Установка колеса llama.cpp. Только по явному действию, без оболочки shell.

Setup.exe по-прежнему несёт CPU-колесо и сам CUDA не скачивает.
Здесь ставится сборка в тот интерпретатор, из которого запущено приложение.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable

from dotlingo.hardware import HardwareSnapshot, has_discrete_nvidia

LLAMA_PIN = "llama-cpp-python==0.3.35"
CPU_INDEX = "https://abetlen.github.io/llama-cpp-python/whl/cpu"
CUDA_INDEX = "https://abetlen.github.io/llama-cpp-python/whl/cu124"
# Готовые CUDA-колёса опубликованы не под каждую версию Python.
CUDA_PYTHON = ((3, 10), (3, 11), (3, 12))
# --force-reinstall на эти пакеты заново качает гигабайты, поэтому шаг отдельный.
CUDA_RUNTIME_PACKAGES = ("nvidia-cublas-cu12", "nvidia-cuda-runtime-cu12")


class RuntimeInstallError(RuntimeError):
    """Установка сборки не выполнена. Текст можно показать пользователю."""


def packaged_app() -> bool:
    """Собранный PyInstaller. Внутри него pip не меняет уже вшитый runtime."""
    return bool(getattr(sys, "frozen", False))


def cuda_wheel_supported(version: tuple[int, int] | None = None) -> bool:
    current = version if version is not None else sys.version_info[:2]
    return current in CUDA_PYTHON


def _pip(*packages: str, extra_index: str | None = None, force: bool = False) -> list[str]:
    command = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    if force:
        command += ["--upgrade", "--force-reinstall"]
    command += list(packages)
    if extra_index:
        command += ["--extra-index-url", extra_index]
    return command


def install_steps(accelerator_id: str) -> list[list[str]]:
    """Команды pip по порядку. Пустой список значит неизвестную сборку."""
    if accelerator_id == "cuda":
        return [
            _pip(*CUDA_RUNTIME_PACKAGES),
            _pip(LLAMA_PIN, extra_index=CUDA_INDEX, force=True),
        ]
    if accelerator_id == "cpu":
        return [_pip(LLAMA_PIN, extra_index=CPU_INDEX, force=True)]
    return []


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
    """Поставить выбранную сборку. Первая ошибка останавливает цепочку."""
    if packaged_app():
        raise RuntimeInstallError(
            "В установленной программе runtime уже внутри сборки. "
            "Сменить колесо можно только в исходном запуске."
        )
    steps = install_steps(accelerator_id)
    if not steps:
        raise RuntimeInstallError("Неизвестная сборка runtime.")
    total = len(steps)
    for index, command in enumerate(steps, start=1):
        label = "библиотеки CUDA" if "nvidia-cublas-cu12" in command else "llama.cpp"
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
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise RuntimeInstallError(f"Не удалось запустить pip для {label}.") from exc
        if completed.returncode != 0:
            output = f"{completed.stderr or ''}\n{completed.stdout or ''}"
            raise RuntimeInstallError(_failure_message(label, output))
