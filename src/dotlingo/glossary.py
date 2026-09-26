from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class GlossaryTerm:
    source: str
    target: str


def protect_terms(text: str, terms: list[GlossaryTerm]) -> tuple[str, dict[str, str]]:
    """Protect user glossary entries during generation for stable, exact terminology."""
    replacements: dict[str, str] = {}
    result = text
    ordered = sorted((term for term in terms if term.source), key=lambda item: len(item.source), reverse=True)
    next_token = 0
    for term in ordered:
        pattern = re.compile(rf"(?<!\w){re.escape(term.source)}(?!\w)", re.IGNORECASE)

        def replace(_: re.Match[str]) -> str:
            nonlocal next_token
            token = f"ZXQTERM{next_token:04d}XZ"
            next_token += 1
            replacements[token] = term.target
            return token

        result = pattern.sub(replace, result)
    return result, replacements


def restore_terms(text: str, replacements: dict[str, str]) -> str:
    result = text
    for token, target in replacements.items():
        count = result.count(token)
        if count != 1:
            raise ValueError("Модель изменила защищённый термин. Повторите этот фрагмент или упростите глоссарий.")
        result = result.replace(token, target)
    return result
