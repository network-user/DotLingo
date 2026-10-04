from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("reportlab")
pytest.importorskip("pdfminer")
pytest.importorskip("pypdfium2")

from dotlingo.pdf_layout import (  # noqa: E402
    FONT_NAME,
    GlyphLine,
    choose_layout,
    fit_words,
    group_paragraphs,
    is_translatable,
    join_line_texts,
    normalize_text,
    place_translations,
)


def test_normalize_collapses_old_style_spacing() -> None:
    assert normalize_text("Dinah  'll  miss  me !") == "Dinah'll miss me!"
    assert normalize_text("it ’s  late") == "it's late"
    assert join_line_texts(["There-", "fore I'm mad."]) == "Therefore I'm mad."


def _line(text: str, x0: float, x1: float, baseline: float, size: float = 12.0) -> GlyphLine:
    return GlyphLine(1, text, x0, baseline - 2, x1, baseline + 9, baseline, size, "ModernMT-Extended", 720)


def test_adjacent_indented_lines_start_new_paragraphs() -> None:
    lines = [
        _line("“How do you know?”", 78, 300, 200),
        _line("“You must be,”", 78, 280, 183),
        _line("or you would not.", 62, 200, 166),
    ]
    paragraphs = group_paragraphs(lines)
    assert [item.source for item in paragraphs] == [
        "“How do you know?”",
        "“You must be,” or you would not.",
    ]


def test_fit_shrinks_until_the_words_fit() -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    from dotlingo.pdf_layout import REGULAR_FONT

    if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(REGULAR_FONT)))
    widths = [80.0, 80.0]
    assert fit_words("один два три четыре пять", widths, FONT_NAME, 18) is None
    size, lines = choose_layout("один два три четыре пять", widths, FONT_NAME, 18, 7)
    assert size < 18
    assert " ".join(line for line in lines if line) == "один два три четыре пять"


def test_book_scope_keeps_page_numbers_and_chrome() -> None:
    book = _line("Alice was not hurt", 60, 300, 200)
    number = _line("7", 40, 52, 470, size=8)
    chrome = GlyphLine(1, "Navigate", 40, 4, 90, 16, 6, 12, "Berkeley-Black", 720)
    assert is_translatable(book, "book")
    assert not is_translatable(number, "book")
    assert not is_translatable(chrome, "book")
    assert is_translatable(chrome, "all")


def test_overlay_embeds_cyrillic(tmp_path: Path) -> None:
    from reportlab.pdfgen import canvas

    from dotlingo.pdf_layout import REGULAR_FONT, _register_font

    _register_font()
    source = tmp_path / "page.pdf"
    sheet = canvas.Canvas(str(source), pagesize=(400, 300))
    sheet.setFont(FONT_NAME, 12)
    sheet.drawString(40, 200, "Hello cat and mouse today.")
    sheet.drawString(40, 40, "Navigate")
    sheet.save()
    output = tmp_path / "out.pdf"
    place_translations(
        source,
        output,
        {(1, 0): "Привет, кот и мышь.", (1, 1): "Навигация"},
        pages=[1],
        scope="all",
    )
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(output))
    text = document[0].get_textpage().get_text_bounded()
    document.close()
    assert "Привет, кот и мышь." in text
    assert "Навигация" in text
    assert REGULAR_FONT.is_file()
