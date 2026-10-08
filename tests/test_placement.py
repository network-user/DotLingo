import os
from pathlib import Path

from dotlingo.engine import InferenceProcess, _safe_backend_error, prepare_llama_env
from dotlingo.hardware import (
    HardwareSnapshot,
    plan_batch,
    plan_context,
    plan_placement,
    plan_threads,
)


def _snapshot(**overrides: object) -> HardwareSnapshot:
    values: dict[str, object] = {
        "cpu_threads": 8,
        "ram_total_gb": 32.0,
        "ram_available_gb": 20.0,
        "disk_free_gb": 100.0,
        "gpu_names": ("NVIDIA GeForce RTX 3060",),
        "gpu_vram_gb": (12.0,),
        "llama_runtime_available": True,
        "llama_gpu_offload_available": True,
        "gpu_vram_free_gb": (10.0,),
    }
    values.update(overrides)
    return HardwareSnapshot(**values)  # type: ignore[arg-type]


def test_threads_leave_one_core_and_stop_at_eight() -> None:
    assert plan_threads(1) == 1
    assert plan_threads(2) == 2
    assert plan_threads(8) == 7
    assert plan_threads(32) == 8


def test_batch_and_context_shrink_on_cpu() -> None:
    model = {"default_context": 8192, "estimated_ram_gb": 8.0, "estimated_vram_gb": 6.5, "layer_count": 32}
    cpu = _snapshot(llama_gpu_offload_available=False, gpu_vram_free_gb=())
    assert plan_batch(0) == 256
    assert plan_batch(-1) == 512
    assert plan_context(model, cpu, 0) == 4096
    assert plan_context(model, _snapshot(), -1) == 8192
    tight = _snapshot(ram_total_gb=9.0)
    assert plan_context(model, tight, -1) == 4096
    placement = plan_placement(cpu, model)
    assert placement["n_ctx"] == 4096
    assert placement["n_batch"] == 256
    assert placement["gpu_layers"] == 0
    assert placement["threads"] == 7


def test_gpu_memory_failure_retries_on_cpu_with_a_shorter_window(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "model.gguf"
    path.write_bytes(b"GGUF" + b"rest")
    engine = InferenceProcess(path, 8192, 4, gpu_layers=-1, n_batch=512)
    seen: list[tuple[int, int, int]] = []

    def launch() -> dict[str, str]:
        seen.append((engine.gpu_layers, engine.context_size, engine.n_batch))
        if len(seen) == 1:
            return {"type": "startup_error", "message": "cuda out of memory"}
        return {"type": "ready"}

    monkeypatch.setattr(engine, "_launch", launch)
    engine.start()
    assert engine.fell_back_to_cpu is True
    assert seen[0] == (-1, 8192, 512)
    assert seen[1] == (0, 4096, 256)


def test_illegal_instruction_asks_for_the_cpu_build() -> None:
    message = _safe_backend_error(OSError("illegal instruction"))
    assert "Процессор" in message


def test_prepare_llama_env_registers_nvidia_pip_bins(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "nvidia" / "cublas" / "bin"
    bin_dir.mkdir(parents=True)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delenv("CUDA_PATH", raising=False)
    added: list[str] = []

    def remember(path: str) -> int:
        added.append(path)
        return 1

    monkeypatch.setattr(os, "add_dll_directory", remember, raising=False)
    prepare_llama_env()
    assert str(bin_dir) in added


def test_prepare_llama_env_skips_dll_registration_off_windows(monkeypatch) -> None:
    monkeypatch.setattr("dotlingo.engine.sys.platform", "linux")
    monkeypatch.delenv("CUDA_PATH", raising=False)
    prepare_llama_env()
