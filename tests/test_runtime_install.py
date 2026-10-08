import subprocess
import sys
from pathlib import Path

import pytest

from dotlingo.hardware import HardwareSnapshot
from dotlingo.runtime_install import (
    RuntimeInstallError,
    _download_verified,
    _WheelRedirect,
    run_install,
    runtime_choices,
    wheel_pins,
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


def test_cpu_and_cuda_wheels_are_pinned_and_only_llama_is_force_reinstalled() -> None:
    cpu = wheel_pins("cpu")
    cuda = wheel_pins("cuda")
    assert len(cpu) == 1
    assert cpu[0]["filename"] == "llama_cpp_python-0.3.35-py3-none-win_amd64.whl"
    assert cpu[0]["sha256"] == "31590ea000d5aff6f05f1e428048e72318a83709288159a5bd4dabec530080bb"
    assert cpu[0]["size"] == 7_086_788
    assert cpu[0]["force"] is True
    assert cpu[0]["url"].startswith("https://github.com/abetlen/")
    assert len(cuda) == 3
    assert [item["force"] for item in cuda] == [False, False, True]
    assert cuda[0]["name"] == "nvidia-cublas-cu12"
    assert cuda[0]["sha256"] == "623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661"
    assert cuda[1]["name"] == "nvidia-cuda-runtime-cu12"
    assert cuda[2]["sha256"] == "84f7218c1e9cf21014b9770c65064c5a3674dd2f4fbc312c5d6bb40cec0fb269"
    assert all(str(item["url"]).startswith("https://") for item in (*cpu, *cuda))
    assert "extra-index-url" not in str(cpu) and "extra-index-url" not in str(cuda)
    assert wheel_pins("vulkan") == []


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
    calls: list[tuple[list[str], dict[str, str]]] = []
    downloaded: list[str] = []
    monkeypatch.setenv("PIP_INDEX_URL", "https://evil.example/simple")
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://evil.example/extra")
    monkeypatch.setenv("PIP_TRUSTED_HOST", "evil.example")

    def fake_download(pin, destination: Path) -> None:
        downloaded.append(str(pin["filename"]))
        destination.write_bytes(b"wheel")

    def fake_run(command, **kwargs):
        calls.append((list(command), dict(kwargs.get("env") or {})))

        class Result:
            returncode = 1
            stderr = "connection timed out"
            stdout = ""

        return Result()

    monkeypatch.setattr("dotlingo.runtime_install._download_verified", fake_download)
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeInstallError, match="скачать"):
        run_install("cuda")
    assert downloaded == ["nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl"]
    assert len(calls) == 1
    command, env = calls[0]
    assert command[0] == sys.executable
    assert command[-1].endswith("nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl")
    assert "--force-reinstall" not in command
    assert "--extra-index-url" not in command
    assert "PIP_INDEX_URL" not in env
    assert "PIP_EXTRA_INDEX_URL" not in env
    assert "PIP_TRUSTED_HOST" not in env


def test_download_rejects_a_bad_hash_and_a_foreign_redirect(tmp_path: Path, monkeypatch) -> None:
    pin = wheel_pins("cpu")[0]

    class Response:
        def geturl(self) -> str:
            return str(pin["url"])

        def read(self, _count: int) -> bytes:
            if self._done:
                return b""
            self._done = True
            return b"not-the-wheel"

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        _done = False

    monkeypatch.setattr("dotlingo.runtime_install._open_wheel_request", lambda request, timeout: Response())
    destination = tmp_path / "wheel.whl"
    with pytest.raises(RuntimeInstallError, match="сумм"):
        _download_verified(pin, destination)
    assert not destination.exists()
    with pytest.raises(RuntimeInstallError, match="неизвестный домен"):
        _WheelRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.example/wheel.whl")


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
