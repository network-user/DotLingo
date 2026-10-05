import os

from dotlingo.engine import prepare_llama_env
from dotlingo.glossary import GlossaryTerm
from dotlingo.hardware import HardwareSnapshot, gpu_layers_for
from dotlingo.task_queue import (
    build_translation_prompt,
    chunk_limit,
    clean_model_output,
    context_char_budget,
    fit_context_window,
    group_segments,
    output_repeats_context,
    output_token_budget,
    select_memory_examples,
    split_packed,
)


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
    assert "taking the provided background information into consideration" in user
    assert "Background Information" in user
    assert "harbour" in user
    assert "The road was long." not in user
    assert "Дорога была долгой." in user
    assert "/no_think" not in user
    background_at = user.index("*[Background Information]*")
    instruction_at = user.index("taking the provided background information into consideration")
    source_at = user.rfind("*[Source Text]*")
    assert background_at < instruction_at < source_at
    assert "harbour" not in user[source_at:]
    assert "Дорога была долгой." not in user[source_at:]
    assert "ZXQTERM0000XZ" in user[source_at:]


def test_hy_mt2_without_context_uses_the_default_template() -> None:
    system, user, _replacements = build_translation_prompt(
        "Hello.",
        "en → ru",
        "",
        "",
        [],
        [],
        style="hy-mt2",
    )
    assert system == ""
    assert "*[Background Information]*" not in user
    assert "*[Source Text]*" not in user
    assert "only output the translated result" in user
    assert user.endswith("Hello.")


def test_scaffold_echo_keeps_the_translation_after_the_source_label() -> None:
    echoed = (
        "*[Информация о фоне]*\n"
        "Недавний перевод, продолжение в том же стиле:\n"
        "Источник: ГЛАВА I.\n"
        "*[Текст источника]*\n"
        "ГЛАВА I"
    )
    assert clean_model_output(echoed) == "ГЛАВА I"
    packed = "Первый абзац.\n\nВторой абзац."
    assert clean_model_output("*[Текст источника]*\n" + packed) == packed
    repeated = "Алиса сидела на берегу и смотрела в книгу сестры без картинок. " * 2
    assert output_repeats_context(repeated, [("Alice sat.", repeated)], "")
    assert not output_repeats_context("Белый кролик достал часы из кармана жилета и побежал дальше по полю.", [("Alice sat.", repeated)], "")


def test_recent_context_stays_out_of_the_stable_system_prompt() -> None:
    system, user, _replacements = build_translation_prompt(
        "Hello.",
        "en → ru",
        "",
        "",
        [],
        [("The road was long.", "Дорога была долгой.")],
        summary="Алиса уже в норе.",
    )
    assert "Дорога была долгой." not in system
    assert "Алиса уже в норе." not in system
    assert "Recent translation, continue in the same voice:" in user
    assert "Story so far" in user
    assert user.endswith("Hello.")


def test_chunk_and_context_budgets_follow_the_model_window() -> None:
    assert chunk_limit(2048) == 700
    assert chunk_limit(4096) == 1600
    assert chunk_limit(8192) == 2400
    assert context_char_budget(2048) == 480
    assert context_char_budget(8192) == 2400
    assert output_token_budget("Hi", 2048) == 128
    assert output_token_budget("x" * 500, 2048) == 500
    assert output_token_budget("x" * 3000, 768) == 768


def test_context_window_keeps_the_newest_pairs_inside_the_budget() -> None:
    pairs = [("one", "a" * 100), ("two", "b" * 100), ("three", "c" * 100)]
    assert fit_context_window(pairs, 0) == []
    assert fit_context_window(pairs, 150) == [("three", "c" * 100)]
    window = fit_context_window(pairs, 250)
    assert [source for source, _translation in window] == ["two", "three"]


def test_short_whole_blocks_pack_and_a_split_paragraph_stays_alone() -> None:
    segments = [
        {"block_ord": 0, "segment_ord": 0, "source": "A" * 100},
        {"block_ord": 0, "segment_ord": 1, "source": "B" * 100},
        {"block_ord": 1, "segment_ord": 0, "source": "C"},
        {"block_ord": 2, "segment_ord": 0, "source": "D"},
        {"block_ord": 3, "segment_ord": 0, "source": "E" * 400},
    ]
    groups = group_segments(segments, 250)
    assert groups[0] == [segments[0]]
    assert groups[1] == [segments[1]]
    assert groups[2] == [segments[2], segments[3]]
    assert groups[3] == [segments[4]]
    assert split_packed("Первый.\n\nВторой.", 2) == ["Первый.", "Второй."]
    assert split_packed("Первый.\nВторой.", 2) is None
    assert split_packed("Один.", 1) == ["Один."]


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
    monkeypatch.setenv("PATH", os.pathsep.join([str(missing / "bin"), os.environ.get("PATH", "")]))
    prepare_llama_env()
    assert "CUDA_PATH" not in os.environ
    assert str(missing / "bin") not in os.environ.get("PATH", "")


def test_chat_runtime_check_ignores_missing_cuda_toolkit(monkeypatch) -> None:
    monkeypatch.setenv("CUDA_PATH", r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6")
    from dotlingo.api import _runtime_available

    assert _runtime_available() is True
    assert "CUDA_PATH" not in os.environ


def test_gpu_layers_follow_free_vram_estimate() -> None:
    model = {"estimated_vram_gb": 6.5, "layer_count": 32}
    assert gpu_layers_for(_snapshot(gpu_vram_free_gb=(7.5,)), model) == -1
    partial = gpu_layers_for(_snapshot(gpu_vram_free_gb=(3.0,)), model)
    assert 4 <= partial < 32
    assert gpu_layers_for(_snapshot(gpu_vram_free_gb=(1.0,)), model) == 0
    assert gpu_layers_for(_snapshot(llama_gpu_offload_available=False), model) == 0
    assert gpu_layers_for(_snapshot(gpu_vram_free_gb=None), model) == 0
