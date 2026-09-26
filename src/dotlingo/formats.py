from __future__ import annotations

import json
import os
import posixpath
import re
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

SUPPORTED_IMPORT = {".txt", ".md", ".markdown", ".docx", ".epub", ".pdf"}
SUPPORTED_EXPORT = {".txt", ".md", ".markdown", ".docx", ".epub"}
MAX_DOCUMENT_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000
MAX_ARCHIVE_UNPACKED = 512 * 1024 * 1024


class DocumentError(ValueError):
    """The selected file is damaged, unsupported, or cannot be converted safely."""


class ScannedPdfError(DocumentError):
    def __init__(self, pages: list[int]) -> None:
        self.pages = pages
        page_list = ", ".join(map(str, pages[:12]))
        suffix = "…" if len(pages) > 12 else ""
        super().__init__(
            "В PDF нет текстового слоя на страницах "
            f"{page_list}{suffix}. OCR не запускался; импорт сканов пока не поддерживается."
        )


@dataclass(frozen=True)
class Block:
    order: int
    section: str
    section_title: str
    kind: str
    text: str
    locator: dict[str, Any] = field(default_factory=dict)
    prefix: str = ""
    suffix: str = ""
    translatable: bool = True


@dataclass(frozen=True)
class ParsedDocument:
    title: str
    format: str
    blocks: tuple[Block, ...]
    warnings: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


def _decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return data.decode("cp1251")
        except UnicodeDecodeError as exc:
            raise DocumentError("Не удалось определить кодировку текста (поддерживаются UTF-8 и CP1251).") from exc


def _plain_blocks(text: str) -> tuple[Block, ...]:
    pieces = re.split(r"(\r?\n[\t ]*\r?\n+)", text)
    blocks: list[Block] = []
    for item in pieces:
        if not item:
            continue
        if item.isspace():
            blocks.append(Block(len(blocks), "main", "Документ", "gap", item, translatable=False))
            continue
        leading = item[: len(item) - len(item.lstrip())]
        trailing = item[len(item.rstrip()) :]
        body_end = len(item) - len(trailing) if trailing else len(item)
        body = item[len(leading) : body_end]
        if not body:
            blocks.append(Block(len(blocks), "main", "Документ", "gap", item, translatable=False))
        else:
            blocks.append(
                Block(len(blocks), "main", "Документ", "paragraph", body, prefix=leading, suffix=trailing)
            )
    return tuple(blocks)


def _markdown_blocks(text: str) -> tuple[Block, ...]:
    lines = text.splitlines(keepends=True)
    if text and not lines:
        lines = [text]
    blocks: list[Block] = []
    section_id, section_title, in_fence = "main", "Документ", False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        fence = re.match(r"^(```+|~~~+)", stripped)
        if fence:
            collected = [line]
            in_fence = not in_fence
            i += 1
            if in_fence:
                while i < len(lines):
                    collected.append(lines[i])
                    if re.match(r"^\s*(```+|~~~+)\s*$", lines[i]):
                        i += 1
                        in_fence = False
                        break
                    i += 1
            blocks.append(
                Block(len(blocks), section_id, section_title, "code", "".join(collected), translatable=False)
            )
            continue
        if in_fence:
            blocks.append(Block(len(blocks), section_id, section_title, "code", line, translatable=False))
            i += 1
            continue
        if not stripped.strip():
            blocks.append(Block(len(blocks), section_id, section_title, "gap", line, translatable=False))
            i += 1
            continue

        marker = re.match(r"^(#{1,6}\s+|>\s+|(?:[-+*]|\d+[.)])\s+)", stripped)
        prefix = indent + (marker.group(0) if marker else "")
        body_line = line[len(prefix) :]
        kind = "heading" if marker and marker.group(0).lstrip().startswith("#") else "paragraph"
        if kind == "heading":
            title = body_line.strip()
            section_id = f"section-{len([b for b in blocks if b.kind == 'heading']) + 1}"
            section_title = title or "Раздел"
        # Join wrapped Markdown lines into one prose block. Lists, quotes and headings
        # remain separate so their syntax can be restored exactly.
        if marker:
            content = body_line
            end = i + 1
        else:
            collected = [body_line]
            end = i + 1
            while end < len(lines) and lines[end].strip():
                next_stripped = lines[end].lstrip()
                if re.match(r"^(#{1,6}\s+|>\s+|(?:[-+*]|\d+[.)])\s+|```+|~~~+)", next_stripped):
                    break
                collected.append(lines[end])
                end += 1
            content = "".join(collected)
        trailing = content[len(content.rstrip("\r\n")) :]
        if trailing:
            content = content[: -len(trailing)]
        blocks.append(
            Block(
                len(blocks),
                section_id,
                section_title,
                kind,
                content,
                prefix=prefix,
                suffix=trailing,
            )
        )
        i = end
    return tuple(blocks)


def _safe_archive(zf: zipfile.ZipFile) -> None:
    infos = zf.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise DocumentError("В архиве слишком много элементов.")
    if sum(item.file_size for item in infos) > MAX_ARCHIVE_UNPACKED:
        raise DocumentError("Распакованный архив слишком велик для безопасного импорта.")
    names: set[str] = set()
    for info in infos:
        normalized = posixpath.normpath(info.filename.replace("\\", "/"))
        if normalized.startswith("../") or normalized.startswith("/") or "\x00" in info.filename:
            raise DocumentError("В архиве найден небезопасный путь.")
        if normalized in names:
            raise DocumentError("В архиве есть повторяющиеся имена файлов.")
        names.add(normalized)


def _docx_paragraphs(container: Any) -> list[Any]:
    from docx.document import Document as DocumentObject
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = container if isinstance(container, DocumentObject) else None
    parent = document.element.body if document else container._tc
    part = document.part if document else container.part
    paragraphs: list[Any] = []
    seen_cells: set[int] = set()
    for child in parent.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraphs.append(Paragraph(child, part))
        elif tag == "tbl":
            table = Table(child, part)
            for row in table.rows:
                for cell in row.cells:
                    cell_identity = id(cell._tc)
                    if cell_identity in seen_cells:
                        continue
                    seen_cells.add(cell_identity)
                    paragraphs.extend(_docx_paragraphs(cell))
    return paragraphs


def _import_docx(path: Path) -> ParsedDocument:
    from docx import Document

    doc = Document(path)
    blocks: list[Block] = []
    section_id, section_title = "main", "Документ"
    for index, paragraph in enumerate(_docx_paragraphs(doc)):
        text = paragraph.text
        style = paragraph.style.name if paragraph.style else ""
        kind = "heading" if style.lower().startswith("heading") or style.lower() == "title" else "paragraph"
        if kind == "heading" and text.strip():
            section_id, section_title = f"section-{index + 1}", text.strip()
        blocks.append(
            Block(
                len(blocks),
                section_id,
                section_title,
                kind,
                text,
                locator={"paragraph": index},
                translatable=bool(text.strip()),
            )
        )
    title = path.stem
    try:
        if doc.core_properties.title:
            title = doc.core_properties.title
    except Exception:
        pass
    return ParsedDocument(title, "docx", tuple(blocks), ("Сохраняются абзацы и таблицы; форматирование внутри смешанных фрагментов может упроститься.",))


_EPUB_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "dt", "dd", "td", "th", "figcaption", "title", "div"}
_EPUB_SKIP = {"pre", "code", "script", "style", "svg", "math"}


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower() if isinstance(element.tag, str) else ""


def _epub_text_elements(root: ET.Element) -> list[ET.Element]:
    all_elements = list(root.iter())
    semantic = [item for item in all_elements if _tag(item) in _EPUB_TAGS]
    result: list[ET.Element] = []
    semantic_ids = {id(item) for item in semantic}
    for element in semantic:
        if any(id(child) in semantic_ids for child in element.iter() if child is not element):
            continue
        if any(_tag(child) in _EPUB_SKIP for child in element.iter()):
            continue
        if "".join(element.itertext()).strip():
            result.append(element)
    return result


def _epub_package(zf: zipfile.ZipFile) -> tuple[str, str, list[tuple[str, str]]]:
    try:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        rootfile = next(item for item in container.iter() if _tag(item) == "rootfile")
        package_path = rootfile.attrib["full-path"]
        package = ET.fromstring(zf.read(package_path))
    except (KeyError, ET.ParseError, StopIteration, AttributeError) as exc:
        raise DocumentError("Повреждённый EPUB: отсутствует container.xml или package document.") from exc
    manifest = {}
    for item in package.iter():
        if _tag(item) == "item" and item.get("id") and item.get("href"):
            href = unquote(urlsplit(item.get("href", "")).path)
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(package_path), href))
            if resolved.startswith("../") or resolved.startswith("/"):
                raise DocumentError("В EPUB найден путь за пределами архива.")
            manifest[item.get("id", "")] = (resolved, item.get("media-type", ""))
    spine: list[tuple[str, str]] = []
    for itemref in package.iter():
        if _tag(itemref) == "itemref":
            entry = manifest.get(itemref.get("idref", ""))
            if entry and entry[1] in {"application/xhtml+xml", "text/html"}:
                spine.append(entry)
    if not spine:
        raise DocumentError("В EPUB не найдены XHTML-разделы в порядке книги.")
    title = next((item.text for item in package.iter() if _tag(item) == "title" and item.text), "")
    return package_path, title, spine


def _import_epub(path: Path) -> ParsedDocument:
    blocks: list[Block] = []
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            _safe_archive(zf)
            _, title, spine = _epub_package(zf)
            for chapter_no, (href, _) in enumerate(spine, 1):
                try:
                    root = ET.fromstring(zf.read(href))
                except (KeyError, ET.ParseError) as exc:
                    raise DocumentError(f"Повреждён раздел EPUB: {href}") from exc
                current_section, current_title = f"chapter-{chapter_no}", f"Глава {chapter_no}"
                for item_index, element in enumerate(_epub_text_elements(root)):
                    text = "".join(element.itertext())
                    tag = _tag(element)
                    kind = "heading" if tag.startswith("h") and len(tag) == 2 else "paragraph"
                    if kind == "heading" and text.strip():
                        current_section, current_title = f"chapter-{chapter_no}-section-{item_index}", text.strip()
                    blocks.append(
                        Block(
                            len(blocks),
                            current_section,
                            current_title,
                            kind,
                            text,
                            locator={"href": href, "element": item_index},
                            translatable=bool(text.strip()),
                        )
                    )
    except (OSError, zipfile.BadZipFile) as exc:
        raise DocumentError("Файл EPUB повреждён или не является ZIP-архивом.") from exc
    warnings.append("Порядок глав, XHTML-структура, ссылки и изображения сохраняются; форматирование внутри абзаца может упроститься.")
    return ParsedDocument(title or path.stem, "epub", tuple(blocks), tuple(warnings))


def _import_pdf(path: Path) -> ParsedDocument:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DocumentError("Импорт PDF требует дополнительной зависимости pypdf. Установите пакет pdf.") from exc
    try:
        doc = PdfReader(str(path), strict=True)
        if doc.is_encrypted:
            raise DocumentError("PDF защищён паролем. Снимите защиту перед импортом.")
        page_texts = [page.extract_text() or "" for page in doc.pages]
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError("Не удалось прочитать PDF. Проверьте, что файл не повреждён или защищён паролем.") from exc
    if not page_texts:
        raise DocumentError("PDF не содержит страниц.")
    image_only = [i + 1 for i, content in enumerate(page_texts) if len(content.strip()) < 12]
    if len(image_only) == len(page_texts):
        raise ScannedPdfError(image_only)
    if image_only:
        raise ScannedPdfError(image_only)
    blocks: list[Block] = []
    for page_no, content in enumerate(page_texts, 1):
        blocks.append(Block(len(blocks), f"page-{page_no}", f"Страница {page_no}", "heading", f"Страница {page_no}"))
        for block in _plain_blocks(content):
            blocks.append(
                Block(
                    len(blocks),
                    f"page-{page_no}",
                    f"Страница {page_no}",
                    block.kind,
                    block.text,
                    prefix=block.prefix,
                    suffix=block.suffix,
                    translatable=block.translatable,
                )
            )
    warning = "Из PDF извлекается только текстовый слой. Вёрстка, таблицы, колонтитулы и изображения не переносятся; PDF-экспорт недоступен."
    return ParsedDocument(path.stem, "pdf", tuple(blocks), (warning,))


def import_document(path: Path) -> ParsedDocument:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_IMPORT:
        raise DocumentError(f"Формат {suffix or '(без расширения)'} не поддерживается для импорта.")
    try:
        if path.stat().st_size > MAX_DOCUMENT_BYTES:
            raise DocumentError("Размер документа больше 512 МБ.")
        if suffix in {".txt", ".md", ".markdown"}:
            text = _decode_text(path.read_bytes())
            blocks = _markdown_blocks(text) if suffix != ".txt" else _plain_blocks(text)
            return ParsedDocument(path.stem, "markdown" if suffix != ".txt" else "txt", blocks)
        if suffix == ".docx":
            return _import_docx(path)
        if suffix == ".epub":
            return _import_epub(path)
        if suffix == ".pdf":
            return _import_pdf(path)
    except OSError as exc:
        raise DocumentError("Не удалось прочитать выбранный файл.") from exc
    raise DocumentError("Неподдерживаемый формат.")


def block_to_dict(block: Block) -> dict[str, Any]:
    return asdict(block)


def block_from_dict(data: dict[str, Any]) -> Block:
    return Block(**data)


def _translation_for(block: Block, translations: dict[int, str]) -> str:
    if not block.translatable:
        return block.text
    return translations.get(block.order, block.text)


def _text_export(parsed: ParsedDocument, translations: dict[int, str], markdown: bool) -> str:
    chunks: list[str] = []
    previous_section = None
    for block in parsed.blocks:
        if markdown and block.section != previous_section and block.section_title and block.kind != "heading":
            if chunks and chunks[-1] and not chunks[-1].endswith("\n\n"):
                chunks.append("\n\n")
            chunks.append(f"## {block.section_title}\n\n")
        previous_section = block.section
        if block.kind == "heading" and markdown:
            body = _translation_for(block, translations).strip()
            heading_prefix = block.prefix if block.prefix.lstrip().startswith("#") else "# "
            chunks.append(f"{heading_prefix}{body}{block.suffix or chr(10)}")
        else:
            chunks.append(block.prefix + _translation_for(block, translations) + block.suffix)
    return "".join(chunks)


def _set_xml_element_text(element: ET.Element, value: str) -> None:
    element.text = value
    for child in element.iter():
        if child is element:
            continue
        child.text = None
        child.tail = None


def _export_epub(source: Path, destination: Path, parsed: ParsedDocument, translations: dict[int, str]) -> None:
    grouped: dict[str, dict[int, str]] = {}
    for block in parsed.blocks:
        if block.translatable and "href" in block.locator:
            grouped.setdefault(str(block.locator["href"]), {})[int(block.locator["element"])] = _translation_for(block, translations)
    try:
        with zipfile.ZipFile(source) as src, zipfile.ZipFile(destination, "w") as dst:
            _safe_archive(src)
            for item in src.infolist():
                data = src.read(item.filename)
                if item.filename in grouped:
                    root = ET.fromstring(data)
                    elements = _epub_text_elements(root)
                    for index, translated in grouped[item.filename].items():
                        if index < len(elements):
                            _set_xml_element_text(elements[index], translated)
                    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                clone = zipfile.ZipInfo(item.filename, item.date_time)
                clone.compress_type = zipfile.ZIP_STORED if item.filename == "mimetype" else item.compress_type
                clone.external_attr = item.external_attr
                clone.comment = item.comment
                dst.writestr(clone, data)
    except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise DocumentError("Не удалось записать EPUB. Исходник сохранён без изменений.") from exc


def _export_docx(source: Path, destination: Path, parsed: ParsedDocument, translations: dict[int, str]) -> None:
    from docx import Document

    doc = Document(source)
    paragraphs = _docx_paragraphs(doc)
    for block in parsed.blocks:
        if not block.translatable:
            continue
        index = int(block.locator.get("paragraph", -1))
        if index < 0 or index >= len(paragraphs):
            continue
        value = _translation_for(block, translations)
        paragraph = paragraphs[index]
        text_nodes = paragraph._p.xpath(".//w:t")
        if text_nodes:
            text_nodes[0].text = value
            for node in text_nodes[1:]:
                node.text = ""
        elif value:
            paragraph.add_run(value)
    doc.save(destination)


def export_document(
    source_path: Path,
    destination: Path,
    parsed: ParsedDocument,
    translations: dict[int, str],
) -> None:
    """Write a separate translated artifact; never modify the saved source copy."""
    destination = Path(destination)
    suffix = destination.suffix.lower()
    if suffix not in SUPPORTED_EXPORT:
        raise DocumentError("Для этого формата нет проверенного экспортёра.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}.{uuid.uuid4().hex}.partial{suffix}")
    if suffix in {".txt", ".md", ".markdown"}:
        content = _text_export(parsed, translations, suffix != ".txt")
        temporary.write_text(content, encoding="utf-8", newline="")
    elif suffix == ".docx":
        if parsed.format != "docx":
            raise DocumentError("DOCX-экспорт доступен только из импортированного DOCX.")
        _export_docx(source_path, temporary, parsed, translations)
    elif suffix == ".epub":
        if parsed.format != "epub":
            raise DocumentError("EPUB-экспорт доступен только из импортированного EPUB.")
        _export_epub(source_path, temporary, parsed, translations)
    os.replace(temporary, destination)


def save_parsed(path: Path, parsed: ParsedDocument) -> None:
    path.write_text(
        json.dumps(
            {
                "title": parsed.title,
                "format": parsed.format,
                "warnings": parsed.warnings,
                "metadata": parsed.metadata,
                "blocks": [block_to_dict(block) for block in parsed.blocks],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def load_parsed(path: Path) -> ParsedDocument:
    data = json.loads(path.read_text(encoding="utf-8"))
    return ParsedDocument(
        data["title"],
        data["format"],
        tuple(block_from_dict(item) for item in data["blocks"]),
        tuple(data.get("warnings", [])),
        data.get("metadata", {}),
    )
