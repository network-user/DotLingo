from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import psutil


@dataclass(frozen=True)
class HardwareSnapshot:
    cpu_threads: int | None
    ram_total_gb: float | None
    ram_available_gb: float | None
    disk_free_gb: float | None
    gpu_names: tuple[str, ...] | None
    gpu_vram_gb: tuple[float | None, ...] | None
    llama_runtime_available: bool
    llama_gpu_offload_available: bool | None
    gpu_vram_free_gb: tuple[float | None, ...] | None = None

    @property
    def profile(self) -> str:
        if self.ram_total_gb is None:
            return "не удалось определить"
        if self.ram_total_gb < 12:
            return "слабое устройство"
        if self.ram_total_gb < 32:
            return "среднее устройство"
        return "мощное устройство"


def _mib_to_gb(value: str) -> float | None:
    try:
        return round(float(value.strip()) / 1024, 1)
    except ValueError:
        return None


def _gpus() -> tuple[
    tuple[str, ...] | None,
    tuple[float | None, ...] | None,
    tuple[float | None, ...] | None,
]:
    command = shutil.which("nvidia-smi")
    if not command:
        return None, None, None
    try:
        result = subprocess.run(
            [
                command,
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            return None, None, None
        names, total, free = [], [], []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            name, total_mib, free_mib = line.rsplit(",", 2)
            names.append(name.strip())
            total.append(_mib_to_gb(total_mib))
            free.append(_mib_to_gb(free_mib))
        if not names:
            return None, None, None
        return tuple(names), tuple(total), tuple(free)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None, None, None


# Запас поверх веса: контекст, буферы и уже занятый рабочий стол.
# Считаем по свободной VRAM, не по полному объёму карты.
VRAM_HEADROOM_GB = 1.4
MIN_DISCRETE_VRAM_GB = 1.5
MIN_PARTIAL_LAYERS = 4
RAM_RESERVE_GB = 1.0
RAM_CONTEXT_MARGIN_GB = 2.0
CPU_CONTEXT_CAP = 4096
CPU_THREAD_CAP = 8
CPU_BATCH = 256
GPU_BATCH = 512
_INTEGRATED_MARKERS = (
    "uhd graphics",
    "hd graphics",
    "iris",
    "vega 8",
    "vega 7",
    "radeon graphics",
)

# Повторные проверки устройства не запускают runtime заново.
_LLAMA_PROBE: tuple[bool, bool | None] | None = None

_LLAMA_PROBE_CODE = (
    "from dotlingo.engine import prepare_llama_env\n"
    "prepare_llama_env()\n"
    "from llama_cpp import llama_supports_gpu_offload\n"
    "print('1' if llama_supports_gpu_offload() else '0')\n"
)


def _probe_llama_gpu() -> bool | None:
    """Спросить GPU-backend в отдельном процессе.

    Импорт llama.cpp в этом процессе надолго забирает GIL, и окно WinForms
    перестаёт отвечать.
    """
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _LLAMA_PROBE_CODE],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    return lines[-1] == "1"


def _llama_gpu_support() -> tuple[bool, bool | None]:
    global _LLAMA_PROBE
    if _LLAMA_PROBE is not None:
        return _LLAMA_PROBE
    if importlib.util.find_spec("llama_cpp") is None:
        _LLAMA_PROBE = (False, None)
        return _LLAMA_PROBE
    _LLAMA_PROBE = (True, _probe_llama_gpu())
    return _LLAMA_PROBE


def reset_llama_probe() -> None:
    """Сбросить ответ пробника после смены колеса llama.cpp."""
    global _LLAMA_PROBE
    _LLAMA_PROBE = None


def detect(data_path: Path) -> HardwareSnapshot:
    """Read device facts and test the selected inference backend independently."""
    try:
        memory = psutil.virtual_memory()
        total, available = round(memory.total / 1024**3, 1), round(memory.available / 1024**3, 1)
    except (OSError, AttributeError):
        total, available = None, None
    try:
        disk_path = Path(data_path)
        while not disk_path.exists() and disk_path.parent != disk_path:
            disk_path = disk_path.parent
        free = round(shutil.disk_usage(disk_path).free / 1024**3, 1)
    except OSError:
        free = None
    threads = os.cpu_count()
    names, vram, vram_free = _gpus()
    runtime, gpu_backend = _llama_gpu_support()
    return HardwareSnapshot(
        threads, total, available, free, names, vram, runtime, gpu_backend, vram_free
    )


def assess_model(snapshot: HardwareSnapshot, model: dict) -> tuple[str, str]:
    """Return a conservative recommendation; RAM/VRAM figures remain estimates."""
    if snapshot.disk_free_gb is None:
        return "unknown", "Свободное место определить не удалось."
    required_disk = float(model["size_bytes"]) / 1024**3 * 1.1
    if snapshot.disk_free_gb < required_disk:
        return "no", f"Для веса и временного файла нужно около {required_disk:.1f} ГБ свободного места."
    if snapshot.ram_total_gb is None:
        return "unknown", "Объём оперативной памяти определить не удалось."
    required_ram = float(model["estimated_ram_gb"])
    if not snapshot.llama_runtime_available:
        return "runtime_missing", "Локальный runtime llama.cpp не установлен."
    if snapshot.ram_total_gb < required_ram + RAM_RESERVE_GB:
        return (
            "no",
            f"На устройстве {snapshot.ram_total_gb:.1f} ГБ RAM, ориентир модели {required_ram:.1f} ГБ "
            f"плюс резерв {RAM_RESERVE_GB:.0f} ГБ.",
        )
    layers = gpu_layers_for(snapshot, model)
    if layers < 0:
        device = "Свободной VRAM хватает на расчётный объём, слои будут отданы GPU."
    elif layers > 0:
        device = f"На GPU уйдёт около {layers} слоёв, остальное останется на CPU."
    else:
        device = "Запуск пойдёт на CPU."
    tight = ""
    if snapshot.ram_available_gb is not None and snapshot.ram_available_gb < required_ram:
        tight = " Сейчас свободно меньше ориентира: перед запуском лучше закрыть другие программы."
    return (
        "cpu_unverified",
        f"Расчётный ориентир RAM ({required_ram:.1f} ГБ) помещается. {device} "
        f"Это оценка размещения, запуск и пиковая память не измерены.{tight}",
    )


def _integrated_gpu(name: str, total_gb: float | None) -> bool:
    """Встройка и карта меньше 1.5 ГБ не считаются бюджетом слоёв."""
    folded = name.casefold()
    if any(marker in folded for marker in _INTEGRATED_MARKERS):
        return True
    return isinstance(total_gb, (int, float)) and float(total_gb) < MIN_DISCRETE_VRAM_GB


def has_discrete_nvidia(snapshot: HardwareSnapshot | None) -> bool:
    """Есть дискретная NVIDIA, на которую имеет смысл ставить CUDA-колесо."""
    if snapshot is None:
        return False
    totals = snapshot.gpu_vram_gb or ()
    for index, name in enumerate(snapshot.gpu_names or ()):
        if "nvidia" not in str(name).casefold():
            continue
        total = totals[index] if index < len(totals) else None
        if _integrated_gpu(str(name), total if isinstance(total, (int, float)) else None):
            continue
        return True
    return False


def _best_free_vram_gb(snapshot: HardwareSnapshot) -> float | None:
    names = snapshot.gpu_names or ()
    totals = snapshot.gpu_vram_gb or ()
    frees = snapshot.gpu_vram_free_gb or ()
    best: float | None = None
    for index, value in enumerate(frees):
        if not isinstance(value, (int, float)):
            continue
        name = names[index] if index < len(names) else ""
        total = totals[index] if index < len(totals) else None
        if _integrated_gpu(str(name), total if isinstance(total, (int, float)) else None):
            continue
        free = float(value)
        best = free if best is None else max(best, free)
    return best


def gpu_layers_for(snapshot: HardwareSnapshot, model: dict) -> int:
    """Сколько слоёв отдать GPU. -1 значит все слои, 0 значит только CPU.

    Решение сравнивает свободную VRAM дискретной карты с расчётным объёмом модели.
    Это не замер.
    """
    if not snapshot.llama_gpu_offload_available:
        return 0
    free = _best_free_vram_gb(snapshot)
    needed = model.get("estimated_vram_gb")
    if free is None or not isinstance(needed, (int, float)) or float(needed) <= 0:
        return 0
    needed_gb = float(needed)
    if free >= needed_gb + VRAM_HEADROOM_GB:
        return -1
    layers = model.get("layer_count")
    if not isinstance(layers, int) or layers <= 0:
        return 0
    budget = free - VRAM_HEADROOM_GB
    if budget <= 0:
        return 0
    count = int(layers * budget / needed_gb)
    if count < MIN_PARTIAL_LAYERS:
        return 0
    return min(layers, count)


def plan_threads(cpu_count: int | None) -> int:
    """Потоки llama.cpp: почти все логические процессоры, одно оставляем окну, потолок 8."""
    if isinstance(cpu_count, int) and cpu_count > 0:
        available = cpu_count
    else:
        available = os.cpu_count() or 2
    available = max(1, available)
    if available <= 2:
        return available
    return max(1, min(CPU_THREAD_CAP, available - 1))


def plan_batch(gpu_layers: int) -> int:
    """На CPU батч меньше, чтобы промпт не раздувал оперативную память."""
    return GPU_BATCH if int(gpu_layers) != 0 else CPU_BATCH


def plan_context(model: dict, snapshot: HardwareSnapshot, gpu_layers: int) -> int:
    """Окно под память. Без GPU и при тесной RAM оно не длиннее 4096.

    Чанки документа нужно резать уже по этому окну, иначе промпт не влезет.
    """
    try:
        default = int(model.get("default_context") or CPU_CONTEXT_CAP)
    except (TypeError, ValueError):
        default = CPU_CONTEXT_CAP
    default = max(512, default)
    if int(gpu_layers) == 0:
        return min(default, CPU_CONTEXT_CAP)
    needed = model.get("estimated_ram_gb")
    ram = snapshot.ram_total_gb
    if (
        isinstance(needed, (int, float))
        and ram is not None
        and ram < float(needed) + RAM_CONTEXT_MARGIN_GB
    ):
        return min(default, CPU_CONTEXT_CAP)
    return default


def plan_placement(snapshot: HardwareSnapshot, model: dict) -> dict[str, int]:
    """Один план для документа и диалога: потоки, слои, батч и контекст."""
    layers = gpu_layers_for(snapshot, model)
    return {
        "threads": plan_threads(snapshot.cpu_threads),
        "gpu_layers": layers,
        "n_batch": plan_batch(layers),
        "n_ctx": plan_context(model, snapshot, layers),
    }


def recommend_model(
    snapshot: HardwareSnapshot,
    models: list[dict],
    installed_ids: set[str] | None = None,
) -> tuple[dict | None, str]:
    """Pick the largest documented model that fits with a small RAM reserve."""
    installed_ids = installed_ids or set()
    candidates = [
        model
        for model in models
        if model.get("status") == "available"
        and isinstance(model.get("estimated_ram_gb"), (int, float))
        and isinstance(model.get("size_bytes"), int)
    ]
    if not candidates:
        return None, "Нет моделей с закреплённым размером и оценкой памяти."
    candidates.sort(key=lambda model: float(model["estimated_ram_gb"]), reverse=True)
    eligible = []
    for model in candidates:
        ram = float(model["estimated_ram_gb"])
        disk_required = int(model["size_bytes"] * 1.1)
        memory_fits = (
            snapshot.ram_total_gb is not None and snapshot.ram_total_gb >= ram + RAM_RESERVE_GB
        )
        disk_fits = (
            model["id"] in installed_ids
            or snapshot.disk_free_gb is not None
            and snapshot.disk_free_gb >= disk_required / 1024**3
        )
        if memory_fits and disk_fits:
            eligible.append(model)
    if eligible:
        choice = eligible[0]
        reason = (
            f"Подобрана самая крупная доступная оценка среди моделей, которым хватает места: "
            f"ориентир {choice['estimated_ram_gb']:.1f} ГБ RAM при {snapshot.ram_total_gb:.1f} ГБ "
            f"на устройстве и резерве 1 ГБ. Значение расчётное, не измерено."
        )
        if not snapshot.llama_runtime_available:
            reason += " Для запуска сначала потребуется llama-cpp-python."
        return choice, reason

    smallest = candidates[-1]
    if snapshot.disk_free_gb is None:
        reason = "Свободное место определить не удалось; выбрана модель с наименьшей расчётной потребностью."
    elif snapshot.ram_total_gb is None:
        reason = "RAM определить не удалось; выбрана модель с наименьшей расчётной потребностью."
    elif snapshot.ram_total_gb < float(smallest["estimated_ram_gb"]) + RAM_RESERVE_GB:
        reason = "Памяти устройства мало даже для минимального резерва; выбрана наименьшая модель."
    else:
        reason = "Свободного места мало для моделей-кандидатов; выбрана наименьшая модель."
    if not snapshot.llama_runtime_available:
        reason += " Для запуска сначала потребуется llama-cpp-python."
    return smallest, reason


def _catalog_rows(models: list[dict]) -> list[dict]:
    """Оставить записи каталога, у которых есть id. Битые строки не роняют план."""
    rows = []
    for model in models:
        if not isinstance(model, dict):
            continue
        model_id = model.get("id")
        if isinstance(model_id, str) and model_id:
            rows.append(model)
    return rows


def _download_candidate(model: dict) -> bool:
    return (
        model.get("status") == "available"
        and isinstance(model.get("estimated_ram_gb"), (int, float))
        and isinstance(model.get("size_bytes"), int)
    )


def plan_setup(
    snapshot: HardwareSnapshot | None,
    models: list[dict],
    installed_ids: set[str] | None = None,
) -> dict[str, str | None]:
    """Решение первого запуска: модель уже есть, её нужно скачать или скачивать нечего.

    Функция не бросает исключения из-за дырявого каталога и не измеряет качество перевода.
    """
    installed_ids = installed_ids or set()
    rows = _catalog_rows(models)
    installed = [model for model in rows if model["id"] in installed_ids]
    if installed:
        installed.sort(
            key=lambda model: float(model["estimated_ram_gb"])
            if isinstance(model.get("estimated_ram_gb"), (int, float))
            else 0.0,
            reverse=True,
        )
        return {
            "action": "ready",
            "modelId": installed[0]["id"],
            "reason": "Модель уже на диске. Дополнительная загрузка не нужна.",
        }

    candidates = [model for model in rows if _download_candidate(model)]
    if not candidates:
        return {
            "action": "skip",
            "modelId": None,
            "reason": "В каталоге нет модели, которую можно скачать. Приложение откроется без веса.",
        }

    if snapshot is None:
        candidates.sort(key=lambda model: float(model["estimated_ram_gb"]))
        return {
            "action": "download",
            "modelId": candidates[0]["id"],
            "reason": "Устройство ещё не измерено. Берём модель с наименьшей оценкой памяти.",
        }

    found, reason = recommend_model(snapshot, candidates, installed_ids)
    ordered = sorted(candidates, key=lambda model: float(model["estimated_ram_gb"]), reverse=True)
    if found is not None:
        ordered = [model for model in ordered if float(model["estimated_ram_gb"]) <= float(found["estimated_ram_gb"])]
    for model in ordered:
        try:
            verdict, detail = assess_model(snapshot, model)
        except (KeyError, TypeError, ValueError):
            continue
        if verdict == "no":
            continue
        note = reason if found is not None and model["id"] == found["id"] else detail
        return {"action": "download", "modelId": model["id"], "reason": note}

    return {
        "action": "skip",
        "modelId": None,
        "reason": (
            "Ни одна модель не помещается в память устройства или на диск. "
            "Приложение откроется без загрузки."
        ),
    }
