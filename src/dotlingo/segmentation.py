from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TextSegment:
    order: int
    text: str


def split_text(text: str, max_chars: int = 1_100) -> list[TextSegment]:
    """Split long prose near sentence boundaries while preserving every source byte of text."""
    if max_chars < 64:
        raise ValueError("max_chars must be at least 64")
    if not text:
        return []
    parts: list[str] = []
    remainder = text
    while len(remainder) > max_chars:
        window = remainder[:max_chars]
        sentence_cuts = [m.end() for m in re.finditer(r"[.!?…][\"'»”)]*\s+", window)]
        sentence_cuts.extend(m.end() for m in re.finditer(r"[。！？]", window))
        sentence_cuts = [cut for cut in sentence_cuts if cut > max_chars // 2]
        if sentence_cuts:
            cut = sentence_cuts[-1]
        else:
            whitespace = [m.end() for m in re.finditer(r"\s+", window) if m.end() > max_chars // 2]
            cut = whitespace[-1] if whitespace else max_chars
        parts.append(remainder[:cut])
        remainder = remainder[cut:]
    if remainder:
        parts.append(remainder)
    if "".join(parts) != text:
        raise AssertionError("segmentation changed the original text")
    return [TextSegment(i, part) for i, part in enumerate(parts)]


def translatable_body(text: str) -> tuple[str, str, str]:
    """Separate whitespace so format writers can restore paragraph spacing exactly."""
    left = text[: len(text) - len(text.lstrip())]
    right = text[len(text.rstrip()) :] if text.strip() else ""
    end = len(text) - len(right) if right else len(text)
    return left, text[len(left) : end], right


def preserve_whitespace(source: str, translated: str) -> str:
    left, _, right = translatable_body(source)
    return left + translated.strip() + right
