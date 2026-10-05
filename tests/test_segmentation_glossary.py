from __future__ import annotations

import pytest

from dotlingo.glossary import GlossaryTerm, protect_terms, restore_terms
from dotlingo.segmentation import preserve_whitespace, split_text


def test_split_text_keeps_exact_source_and_prefers_sentence_boundaries() -> None:
    source = ("Первая длинная фраза с точкой. " * 18) + "  хвост\n"
    segments = split_text(source, max_chars=100)
    assert len(segments) > 1
    assert "".join(item.text for item in segments) == source
    assert all(item.text for item in segments)


def test_split_text_breaks_on_cjk_punctuation() -> None:
    source = ("甲" * 80) + "。" + ("乙" * 80)
    segments = split_text(source, max_chars=100)
    assert "".join(item.text for item in segments) == source
    assert segments[0].text.endswith("。")


def test_split_text_rejects_too_small_limit() -> None:
    with pytest.raises(ValueError):
        split_text("text", max_chars=12)


def test_glossary_protection_is_case_insensitive_and_restores_exact_form() -> None:
    text, replacements = protect_terms(
        "Alice met ALICE in a New York café.",
        [GlossaryTerm("Alice", "Алиса"), GlossaryTerm("New York", "Нью-Йорк")],
    )
    assert "Alice" not in text
    assert len(replacements) == 3
    restored = restore_terms(text, replacements)
    assert restored == "Алиса met Алиса in a Нью-Йорк café."


@pytest.mark.parametrize("output", ["", "ZXQTERM0000XZ ZXQTERM0000XZ"])
def test_glossary_rejects_missing_or_duplicated_marker(output: str) -> None:
    with pytest.raises(ValueError):
        restore_terms(output, {"ZXQTERM0000XZ": "термин"})


def test_preserve_whitespace_keeps_source_edges() -> None:
    assert preserve_whitespace("  source\n", "  translation  ") == "  translation\n"
