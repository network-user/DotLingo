from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
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


def _llama_gpu_support() -> tuple[bool, bool | None]:
    if importlib.util.find_spec("llama_cpp") is None:
        return False, None
    try:
        from llama_cpp import llama_supports_gpu_offload

        return True, bool(llama_supports_gpu_offload())
    except (ImportError, AttributeError, OSError):
        return True, None


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
    if snapshot.ram_available_gb is None:
        return "unknown", "Доступную оперативную память определить не удалось."
    required_ram = float(model["estimated_ram_gb"])
    if not snapshot.llama_runtime_available:
        return "runtime_missing", "Локальный runtime llama.cpp не установлен."
    if snapshot.ram_available_gb < required_ram:
        return "no", f"Сейчас доступно {snapshot.ram_available_gb:.1f} ГБ RAM; ориентир модели — {required_ram:.1f} ГБ."
    layers = gpu_layers_for(snapshot, model)
    if layers < 0:
        device = "Свободной VRAM хватает на расчётный объём, слои будут отданы GPU."
    elif layers > 0:
        device = f"На GPU уйдёт около {layers} слоёв, остальное останется на CPU."
    else:
        device = "Запуск пойдёт на CPU."
    return (
        "cpu_unverified",
        f"Расчётный ориентир RAM ({required_ram:.1f} ГБ) помещается. {device} "
        "Это оценка размещения, запуск и пиковая память не измерены.",
    )


def gpu_layers_for(snapshot: HardwareSnapshot, model: dict) -> int:
    """Сколько слоёв отдать GPU. -1 значит все слои, 0 значит только CPU.

    Решение сравнивает свободную VRAM с расчётным объёмом модели. Это не замер.
    """
    if not snapshot.llama_gpu_offload_available:
        return 0
    free_values = [
        value
        for value in (snapshot.gpu_vram_free_gb or ())
        if isinstance(value, (int, float))
    ]
    needed = model.get("estimated_vram_gb")
    if not free_values or not isinstance(needed, (int, float)) or float(needed) <= 0:
        return 0
    free = max(free_values)
    reserve = 0.6
    if free >= float(needed) + reserve:
        return -1
    layers = model.get("layer_count")
    if not isinstance(layers, int) or layers <= 0:
        return 0
    usable = free - reserve
    if usable < 1.2:
        return 0
    count = int(layers * min(0.85, usable / float(needed)))
    return count if count >= 4 else 0


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
        memory_fits = snapshot.ram_available_gb is not None and snapshot.ram_available_gb >= ram + 1.0
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
            f"ориентир {choice['estimated_ram_gb']:.1f} ГБ RAM при свободных "
            f"{snapshot.ram_available_gb:.1f} ГБ с резервом 1 ГБ. Значение расчётное, не измерено."
        )
        if not snapshot.llama_runtime_available:
            reason += " Для запуска сначала потребуется llama-cpp-python."
        return choice, reason

    smallest = candidates[-1]
    if snapshot.disk_free_gb is None:
        reason = "Свободное место определить не удалось; выбрана модель с наименьшей расчётной потребностью."
    elif snapshot.ram_available_gb is None:
        reason = "RAM определить не удалось; выбрана модель с наименьшей расчётной потребностью."
    elif snapshot.ram_available_gb < float(smallest["estimated_ram_gb"]) + 1.0:
        reason = "Свободной RAM мало даже для минимального резерва; выбрана наименьшая модель."
    else:
        reason = "Свободного места мало для моделей-кандидатов; выбрана наименьшая модель."
    if not snapshot.llama_runtime_available:
        reason += " Для запуска сначала потребуется llama-cpp-python."
    return smallest, reason
