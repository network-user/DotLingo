from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from dotlingo.formats import (
    DocumentError,
    ScannedPdfError,
    export_document,
    import_document,
)


def test_txt_crlf_import_translation_export_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "book.txt"
    original = "  First paragraph.\r\n\r\nSecond paragraph.  \n"
    source.write_bytes(original.encode("utf-8"))
    parsed = import_document(source)
    assert [block.text for block in parsed.blocks if block.translatable] == ["First paragraph.", "Second paragraph."]
    output = tmp_path / "translated.txt"
    export_document(source, output, parsed, {0: "Первый абзац.", 2: "Второй абзац."})
    assert output.read_bytes() == "  Первый абзац.\r\n\r\nВторой абзац.  \n".encode("utf-8")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == hashlib.sha256(original.encode()).hexdigest()


def test_cp1251_import_is_supported(tmp_path: Path) -> None:
    source = tmp_path / "legacy.txt"
    source.write_bytes("Привет мир".encode("cp1251"))
    assert import_document(source).blocks[0].text == "Привет мир"


def test_markdown_export_keeps_heading_level_and_code_fence(tmp_path: Path) -> None:
    source = tmp_path / "notes.md"
    source.write_text("## Глава\n\nПервый абзац.\n\n```py\nx = 1\n```\n", encoding="utf-8")
    parsed = import_document(source)
    translations = {block.order: f"Перевод {block.order}" for block in parsed.blocks if block.translatable}
    output = tmp_path / "translated.md"
    export_document(source, output, parsed, translations)
    content = output.read_text(encoding="utf-8")
    assert content.startswith("## Перевод ")
    assert "```py\nx = 1\n```" in content


def test_docx_import_and_export_preserves_table_and_does_not_mutate_source(tmp_path: Path) -> None:
    docx = pytest.importorskip("docx")
    source = tmp_path / "sample.docx"
    document = docx.Document()
    document.add_heading("Chapter", level=1)
    document.add_paragraph("A paragraph.")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Left cell"
    table.cell(0, 1).text = "Right cell"
    document.save(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    parsed = import_document(source)
    assert any("смешанных фрагментов" in warning.lower() for warning in parsed.warnings)
    table_blocks = [block for block in parsed.blocks if "cell" in block.text.lower()]
    assert len(table_blocks) == 2
    translations = {block.order: f"T:{block.text}" for block in parsed.blocks if block.translatable}
    destination = tmp_path / "translated.docx"
    export_document(source, destination, parsed, translations)
    translated = docx.Document(destination)
    text = "\n".join(paragraph.text for paragraph in translated.paragraphs)
    table_text = " ".join(cell.text for row in translated.tables[0].rows for cell in row.cells)
    assert "T:Chapter" in text and "T:A paragraph." in text
    assert "T:Left cell" in table_text and "T:Right cell" in table_text
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


def _make_epub(path: Path) -> None:
    container = '''<?xml version="1.0"?>
    <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
      <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
    </container>'''
    package = '''<?xml version="1.0"?>
    <package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
      <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Fixture</dc:title></metadata>
      <manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/><item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/></manifest>
      <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
    </package>'''
    first = '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Chapter one</h1><p>Hello <em>world</em>.</p></body></html>'
    second = '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Chapter two</h1><p>Second text.</p></body></html>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", package)
        archive.writestr("OEBPS/c1.xhtml", first)
        archive.writestr("OEBPS/c2.xhtml", second)
        archive.writestr("OEBPS/image.svg", "<svg />")


def test_epub_round_trip_keeps_spine_order_and_non_text_assets(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    _make_epub(source)
    parsed = import_document(source)
    headings = [block.text for block in parsed.blocks if block.kind == "heading"]
    assert headings == ["Chapter one", "Chapter two"]
    translations = {block.order: f"T:{block.text}" for block in parsed.blocks if block.translatable}
    output = tmp_path / "translated.epub"
    export_document(source, output, parsed, translations)
    with zipfile.ZipFile(output) as archive:
        assert archive.read("mimetype") == b"application/epub+zip"
        assert archive.read("OEBPS/image.svg") == b"<svg />"
        chapter1 = ET.fromstring(archive.read("OEBPS/c1.xhtml"))
        chapter2 = ET.fromstring(archive.read("OEBPS/c2.xhtml"))
        assert "T:Chapter one" in "".join(chapter1.itertext())
        assert "T:Hello world." in "".join(chapter1.itertext())
        assert "T:Chapter two" in "".join(chapter2.itertext())


def test_pdf_text_layer_supported_and_scan_explicitly_rejected(tmp_path: Path) -> None:
    pytest.importorskip("pypdf")
    fitz = pytest.importorskip("fitz")
    text_pdf = tmp_path / "text.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "This is searchable text.")
        document.save(text_pdf)
    parsed = import_document(text_pdf)
    assert any("текстовый слой" in item.lower() for item in parsed.warnings)
    with pytest.raises(DocumentError):
        export_document(text_pdf, tmp_path / "translated.pdf", parsed, {})

    scan_pdf = tmp_path / "scan.pdf"
    with fitz.open() as document:
        document.new_page()
        document.save(scan_pdf)
    with pytest.raises(ScannedPdfError) as error:
        import_document(scan_pdf)
    assert error.value.pages == [1]
    assert "OCR не запускался" in str(error.value)


def test_corrupt_pdf_is_rejected(tmp_path: Path) -> None:
    pytest.importorskip("pypdf")
    source = tmp_path / "broken.pdf"
    source.write_bytes(b"not a PDF")
    with pytest.raises(DocumentError, match="Не удалось прочитать PDF"):
        import_document(source)


def test_corrupt_epub_is_rejected_without_copying(tmp_path: Path) -> None:
    source = tmp_path / "broken.epub"
    source.write_bytes(b"not a zip")
    with pytest.raises(DocumentError):
        import_document(source)
