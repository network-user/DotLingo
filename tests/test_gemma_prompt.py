import pytest

from dotlingo.engine import render_gemma_turns
from dotlingo.task_queue import GlossaryTerm, build_translation_prompt


def test_gemma_prompt_matches_the_published_text_form() -> None:
    system, user, replacements = build_translation_prompt(
        "Hello.",
        "en → ru",
        "",
        "",
        [],
        [],
        profile="gemma",
    )
    assert system == ""
    assert replacements == {}
    assert user == (
        "You are a professional English (en) to Russian (ru) translator.\n"
        "Your goal is to accurately convey the meaning and nuances of the original English text "
        "while adhering to Russian grammar, vocabulary, and cultural sensitivities.\n"
        "Produce only the Russian translation, without any additional explanations or commentary. "
        "Please translate the following English text into Russian:\n"
        "\n"
        "\n"
        "Hello."
    )


def test_gemma_prompt_keeps_glossary_markers_before_the_source() -> None:
    system, user, replacements = build_translation_prompt(
        "Hello.",
        "en → ru",
        "",
        "",
        [GlossaryTerm("Hello", "Здравствуйте")],
        [],
        profile="gemma",
    )
    assert system == ""
    assert replacements
    assert "Keep every ZXQTERM0000XZ style marker" in user
    assert "Project glossary (mandatory forms): Hello → Здравствуйте" in user
    assert user.endswith("\n\n\nZXQTERM0000XZ.")
    assert "Please translate the following the original language" not in user


def test_gemma_auto_source_does_not_invent_a_language_code() -> None:
    _system, user, _replacements = build_translation_prompt(
        "Hello.",
        "auto → ru",
        "",
        "",
        [],
        [],
        profile="gemma",
    )
    assert "Identify the source language from the text." in user
    assert "(auto)" not in user
    assert "Please translate the following text into Russian:" in user
    assert user.endswith("\n\n\nHello.")


def test_gemma_turn_template_wraps_a_plain_user_message() -> None:
    pytest.importorskip("llama_cpp")
    prompt = render_gemma_turns(
        [{"role": "user", "content": "Hello."}],
        bos_token="<bos>",
        eos_token="<end_of_turn>",
    )
    assert prompt == "<bos><start_of_turn>user\nHello.<end_of_turn>\n<start_of_turn>model\n"


def test_generic_prompt_keeps_the_system_role() -> None:
    system, user, _replacements = build_translation_prompt(
        "Hello.",
        "en → ru",
        "",
        "",
        [],
        [],
    )
    assert system.startswith("You are a professional literary")
    assert user == "Hello."
