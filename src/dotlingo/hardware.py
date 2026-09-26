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

    @property
    def profile(self) -> str:
        if self.ram_total_gb is None:
            return "не удалось определить"
        if self.ram_total_gb < 12:
            return "слабое устройство"
        if self.ram_total_gb < 32:
            return "среднее устройство"
        if self.llama_gpu_offload_available:
            return "мощная система (backend сообщает GPU-поддержку)"
        return "мощный CPU"


def _gpus() -> tuple[tuple[str, ...] | None, tuple[float | None, ...] | None]:
    command = shutil.which("nvidia-smi")
    if not command:
        return None, None
    try:
        result = subprocess.run(
            [command, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            return None, None
        rows = [line.rsplit(",", 1) for line in result.stdout.splitlines() if line.strip()]
        names, memory = [], []
        for name, size in rows:
            names.append(name.strip())
            try:
                memory.append(round(float(size.strip()) / 1024, 1))
            except ValueError:
                memory.append(None)
        return (tuple(names), tuple(memory)) if names else (None, None)
    except (OSError, subprocess.TimeoutExpired):
        return None, None


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
        free = round(shutil.disk_usage(data_path).free / 1024**3, 1)
    except OSError:
        free = None
    threads = os.cpu_count()
    names, vram = _gpus()
    runtime, gpu_backend = _llama_gpu_support()
    return HardwareSnapshot(threads, total, available, free, names, vram, runtime, gpu_backend)


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
    if snapshot.llama_gpu_offload_available:
        return "gpu_unverified", "Backend собран с GPU-поддержкой; запуск этой модели, контекст и VRAM не проверены."
    return "cpu_unverified", f"Расчётный ориентир RAM ({required_ram:.1f} ГБ) помещается, но CPU-запуск и пиковая память этой модели не проверены."
