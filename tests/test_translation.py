import os

from dotlingo.engine import prepare_llama_env
from dotlingo.glossary import GlossaryTerm
from dotlingo.hardware import HardwareSnapshot, gpu_layers_for
from dotlingo.task_queue import build_translation_prompt, select_memory_examples


def _snapshot(**overrides: object) -> HardwareSnapshot:
    values: dict[str, object] = {
        "cpu_threads": 8,
        "ram_total_gb": 32.0,
        "ram_available_gb": 20.0,
        "disk_free_gb": 100.0,
        "gpu_names": ("NVIDIA Demo",),
        "gpu_vram_gb": (8.0,),
        "llama_runtime_available": True,
        "llama_gpu_offload_available": True,
        "gpu_vram_free_gb": (7.5,),
    }
    values.update(overrides)
    return HardwareSnapshot(**values)  # type: ignore[arg-type]


def test_hy_mt2_prompt_is_a_single_user_translation_request() -> None:
    system, user, replacements = build_translation_prompt(
        "The river is wide.",
        "en → ru",
        "Роман о севере.",
        "Сохраняй имена.",
        [GlossaryTerm("river", "река")],
        [("The road was long.", "Дорога была долгой.")],
        style="hy-mt2",
        memory=[("The harbour master nodded.", "Начальник гавани кивнул.")],
    )
    assert system == ""
    assert replacements == {"ZXQTERM0000XZ": "река"}
    assert "ZXQTERM0000XZ" in user
    assert "Russian" in user
    assert "ONLY output the translated result" in user
    assert "Background Information" in user
    assert "harbour" in user
    assert "/no_think" not in user


def test_memory_picks_overlapping_confirmed_pairs_and_skips_the_same_source() -> None:
    pairs = [
        ("The harbour master kept the ledger.", "Начальник гавани вёл книгу."),
        ("The river is wide.", "Река широкая."),
        ("A short note.", "Короткая заметка."),
    ]
    chosen = select_memory_examples("The harbour master closed the ledger.", pairs)
    assert chosen
    assert chosen[0][0].casefold().startswith("the harbour")
    assert all("река" not in item[1].casefold() for item in chosen)
    assert select_memory_examples("The river is wide.", pairs) == []


def test_missing_cuda_toolkit_path_is_ignored(monkeypatch, tmp_path) -> None:
    missing = tmp_path / "cuda-missing"
    monkeypatch.setenv("CUDA_PATH", str(missing))
    prepare_llama_env()
    assert "CUDA_PATH" not in os.environ


def test_gpu_layers_follow_free_vram_estimate() -> None:
    model = {"estimated_vram_gb": 6.5, "layer_count": 32}
    assert gpu_layers_for(_snapshot(gpu_vram_free_gb=(7.5,)), model) == -1
    partial = gpu_layers_for(_snapshot(gpu_vram_free_gb=(3.0,)), model)
    assert 4 <= partial < 32
    assert gpu_layers_for(_snapshot(gpu_vram_free_gb=(1.0,)), model) == 0
    assert gpu_layers_for(_snapshot(llama_gpu_offload_available=False), model) == 0
    assert gpu_layers_for(_snapshot(gpu_vram_free_gb=None), model) == 0
