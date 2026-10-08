import subprocess
import sys
from pathlib import Path

import pytest

from dotlingo.hardware import HardwareSnapshot
from dotlingo.runtime_install import (
    RuntimeInstallError,
    install_steps,
    run_install,
    runtime_choices,
)


def _nvidia() -> HardwareSnapshot:
    return HardwareSnapshot(
        cpu_threads=8,
        ram_total_gb=32.0,
        ram_available_gb=16.0,
        disk_free_gb=40.0,
        gpu_names=("NVIDIA GeForce RTX 3060",),
        gpu_vram_gb=(12.0,),
        llama_runtime_available=False,
        llama_gpu_offload_available=None,
        gpu_vram_free_gb=(8.0,),
    )


def test_cpu_and_cuda_steps_keep_runtime_packages_out_of_force_reinstall() -> None:
    cpu = install_steps("cpu")
    cuda = install_steps("cuda")
    assert len(cpu) == 1
    assert "llama-cpp-python==0.3.35" in cpu[0]
    assert "https://abetlen.github.io/llama-cpp-python/whl/cpu" in cpu[0]
    assert "--force-reinstall" in cpu[0]
    assert len(cuda) == 2
    assert "--force-reinstall" not in cuda[0]
    assert "nvidia-cublas-cu12" in cuda[0]
    assert "nvidia-cuda-runtime-cu12" in cuda[0]
    assert "--force-reinstall" in cuda[1]
    assert "https://abetlen.github.io/llama-cpp-python/whl/cu124" in cuda[1]
    assert install_steps("vulkan") == []
    assert cpu[0][0] == sys.executable
    assert cuda[0][0] == sys.executable


def test_cuda_is_offered_only_for_a_discrete_nvidia_and_a_supported_python() -> None:
    choices = runtime_choices(_nvidia())
    by_id = {item["id"]: item for item in choices["choices"]}
    assert by_id["cpu"]["available"] is True
    if sys.version_info[:2] in {(3, 10), (3, 11), (3, 12)}:
        assert by_id["cuda"]["available"] is True
        assert choices["recommended"] == "cuda"
    else:
        assert by_id["cuda"]["available"] is False
        assert choices["recommended"] == "cpu"
    integrated = HardwareSnapshot(
        cpu_threads=4,
        ram_total_gb=16.0,
        ram_available_gb=8.0,
        disk_free_gb=20.0,
        gpu_names=("Intel UHD Graphics",),
        gpu_vram_gb=(1.0,),
        llama_runtime_available=False,
        llama_gpu_offload_available=None,
        gpu_vram_free_gb=(0.5,),
    )
    plain = runtime_choices(integrated)
    assert plain["recommended"] == "cpu"
    assert all(item["id"] != "cuda" or not item["available"] for item in plain["choices"])


def test_run_install_stops_after_the_first_failed_step(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))

        class Result:
            returncode = 1
            stderr = "connection timed out"
            stdout = ""

        return Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeInstallError, match="скачать"):
        run_install("cuda")
    assert len(calls) == 1
    assert "nvidia-cublas-cu12" in calls[0]
    assert "--force-reinstall" not in calls[0]


def test_packaged_app_refuses_to_replace_the_bundled_wheel(monkeypatch) -> None:
    monkeypatch.setattr("dotlingo.runtime_install.packaged_app", lambda: True)
    options = runtime_choices(_nvidia())
    assert options["frozen"] is True
    assert options["recommended"] is None
    with pytest.raises(RuntimeInstallError, match="внутри сборки"):
        run_install("cpu")


def test_api_install_runtime_runs_only_an_available_choice(tmp_path: Path, monkeypatch) -> None:
    from dotlingo.api import Api

    api = Api(tmp_path)
    api._spawn = lambda work, name: work()  # noqa: SLF001
    calls: list[str] = []
    def remember(accelerator_id, on_progress=None):
        calls.append(accelerator_id)

    monkeypatch.setattr("dotlingo.api.run_install", remember)
    monkeypatch.setattr(
        "dotlingo.api.detect",
        lambda path: HardwareSnapshot(
            cpu_threads=8,
            ram_total_gb=16.0,
            ram_available_gb=8.0,
            disk_free_gb=40.0,
            gpu_names=(),
            gpu_vram_gb=(),
            llama_runtime_available=True,
            llama_gpu_offload_available=False,
        ),
    )
    assert api.installRuntime("vulkan")["ok"] is False
    started = api.installRuntime("cpu")
    assert started["ok"] is True
    assert started["data"]["started"] is True
    assert calls == ["cpu"]
    api._runtime_installing = True
    busy = api.installRuntime("cpu")
    assert busy["ok"] is False
    assert busy["code"] == "busy"
