from dotlingo.engine import force_closed_think, prepare_llama_env
from dotlingo.glossary import GlossaryTerm
from dotlingo.task_queue import build_translation_prompt

_QWEN3_TAIL = (
    "{%- if add_generation_prompt %}\n"
    "{{- '<|im_start|>assistant\\n' }}\n"
    "{%- if enable_thinking is defined and enable_thinking is false %}\n"
    "{{- '<think>\\n\\n</think>\\n\\n' }}\n"
    "{%- endif %}\n"
    "{%- endif %}\n"
)


def test_qwen3_template_starts_after_an_empty_think_block() -> None:
    forced = force_closed_think(_QWEN3_TAIL)
    assert forced is not None
    assert "enable_thinking is defined" not in forced
    assert "<think>" in forced
    prepare_llama_env()
    from llama_cpp.llama_chat_format import Jinja2ChatFormatter

    prompt = str(
        Jinja2ChatFormatter(
            template=(
                "{%- if messages[0].role == 'system' %}"
                "{{- '<|im_start|>system\\n' + messages[0].content + '<|im_end|>\\n' }}"
                "{%- endif %}"
                "{%- for message in messages %}"
                "{%- if message.role == 'user' %}"
                "{{- '<|im_start|>user\\n' + message.content + '<|im_end|>\\n' }}"
                "{%- endif %}"
                "{%- endfor %}"
                + (forced or "")
            ),
            eos_token="<|im_end|>",
            bos_token="",
        )(
            messages=[
                {"role": "system", "content": "Translate into Russian."},
                {"role": "user", "content": "Hello."},
            ]
        ).prompt
    )
    assert prompt.endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n")
    assert "/no_think" not in prompt
    assert "Hello." in prompt


def test_template_without_a_thinking_switch_stays_unchanged() -> None:
    assert force_closed_think("{{ messages[0].content }}") is None


def test_generic_prompt_mentions_markers_only_when_they_are_in_the_source() -> None:
    plain, _user, _replacements = build_translation_prompt(
        "Hello.",
        "en → ru",
        "",
        "",
        [],
        [],
    )
    assert "ZXQTERM0000XZ" not in plain
    marked, user, replacements = build_translation_prompt(
        "The river is wide.",
        "en → ru",
        "",
        "",
        [GlossaryTerm("river", "река")],
        [],
    )
    assert replacements
    assert "ZXQTERM0000XZ" in marked
    assert "ZXQTERM0000XZ" in user
