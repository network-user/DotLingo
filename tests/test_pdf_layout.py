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


def _line(
    text: str,
    x0: float,
    x1: float,
    baseline: float,
    size: float = 12.0,
    page: int = 1,
) -> GlyphLine:
    return GlyphLine(
        page, text, x0, baseline - 2, x1, baseline + 9, baseline, size, "ModernMT-Extended", 720
    )


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


def test_signature_is_not_book_text() -> None:
    assert not is_translatable(_line("B", 520, 536, 70, size=8.2), "book")
    assert not is_translatable(_line("B 2", 510, 540, 70, size=8.1), "all")
    assert is_translatable(_line("I am Alice.", 60, 220, 200), "book")


def test_sentence_continues_across_the_page_and_the_header() -> None:
    lines = [
        _line("pictures or conversations in it, “and what is", 408, 680, 80, page=1),
        _line("DOWN THE", 150, 220, 470, size=8.2, page=2),
        _line("the use of a book,” thought Alice.", 62, 340, 400, page=2),
    ]
    paragraphs = group_paragraphs(lines)
    sources = [item.source for item in paragraphs]
    assert "DOWN THE" in sources
    body = next(item for item in paragraphs if item.kind == "body")
    assert body.source.startswith("pictures or conversations")
    assert "the use of a book" in body.source
    assert [line.page for line in body.lines] == [1, 2]
    assert body.page == 1
    assert body.line_rights == [680.0, 340.0]


def test_sentence_continues_in_the_next_column() -> None:
    lines = [
        _line("taken a watch out of it, and,", 62, 340, 80),
        _line("burning with curiosity, she ran across the field.", 408, 680, 400),
    ]
    paragraphs = group_paragraphs(lines)
    assert len(paragraphs) == 1
    assert paragraphs[0].source.endswith("across the field.")
    assert paragraphs[0].line_rights == [340.0, 680.0]


def test_finished_sentence_stays_its_own_paragraph() -> None:
    lines = [
        _line("Alice was beginning to get very tired.", 60, 340, 200, page=1),
        _line("So she was considering in her own mind.", 60, 360, 400, page=2),
    ]
    paragraphs = group_paragraphs(lines)
    assert [item.source for item in paragraphs] == [
        "Alice was beginning to get very tired.",
        "So she was considering in her own mind.",
    ]


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


def test_spanned_sentence_is_drawn_once(tmp_path: Path) -> None:
    from reportlab.pdfgen import canvas

    source = tmp_path / "span.pdf"
    sheet = canvas.Canvas(str(source), pagesize=(400, 300))
    sheet.setFont("Helvetica", 12)
    sheet.drawString(40, 200, "and what is")
    sheet.showPage()
    sheet.setFont("Helvetica", 12)
    sheet.drawString(40, 200, "the use of a book.")
    sheet.save()
    from dotlingo.pdf_layout import extract_paragraphs

    paragraphs = extract_paragraphs(source, [1, 2], "all")
    assert len(paragraphs) == 1
    assert "the use of a book" in paragraphs[0].source
    output = tmp_path / "out.pdf"
    place_translations(
        source,
        output,
        {paragraphs[0].key: "Толк."},
        pages=[1, 2],
        scope="all",
    )
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(output))
    pages = [document[index].get_textpage().get_text_bounded() for index in range(len(document))]
    document.close()
    assert sum(page.count("Толк") for page in pages) == 1
    assert "Толк" in pages[0]
