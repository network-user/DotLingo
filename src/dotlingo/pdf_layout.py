"""Укладка перевода в текстовый PDF без перерисовки иллюстраций.

Строки книги закрываются цветом бумаги и набираются заново шрифтом
Old Standard (OFL, Alexey Kryukov) в те же полосы. Абзац, который не
входит в свою строку, не срывает файл: он дописывается в конец PDF.
Режим ``book`` не трогает интерфейс и служебные шрифты. Режим ``all``
переводит весь текст.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
REGULAR_FONT = FONT_DIR / "OldStandard-Regular.ttf"
FONT_NAME = "OldStandard"

_BOOK_FONT = "ModernMT"
_CHROME_FONTS = ("Berkeley", "Courier")
_APOSTROPHE = re.compile(
    r"(?<=\w)\s+['’]\s*(?=(?:ll|ve|re|d|m|s|t)\b)",
    re.IGNORECASE,
)
_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?])")
_AFTER_QUOTE = re.compile(r"([«“\"])\s+")
_LIGATURES = str.maketrans({"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"})
_LETTERSPACED = re.compile(
    r"(?<![A-Za-zА-Яа-яЁё])([A-ZА-ЯЁ](?:[ \t][A-ZА-ЯЁ]){3,})(?![A-Za-zА-Яа-яЁё])"
)


class LayoutError(Exception):
    """Текст не помещается в полосу или PDF нельзя собрать."""


@dataclass(frozen=True)
class GlyphLine:
    page: int
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    baseline: float
    size: float
    font: str
    page_width: float

    @property
    def center(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class Paragraph:
    page: int
    index: int
    lines: list[GlyphLine]
    source: str
    kind: str
    measure_right: float = 0.0
    fitted_size: float = 0.0
    line_rights: list[float] = field(default_factory=list)
    fitted_lines: list[str] = field(default_factory=list)
    planned: bool = False

    @property
    def key(self) -> tuple[int, int]:
        return (self.page, self.index)


def _collapse_letterspacing(text: str) -> str:
    """Склеивает «C H A P T E R», но не трогает слово после двойного пробела."""
    return _LETTERSPACED.sub(
        lambda match: match.group(1).replace(" ", "").replace("\t", ""),
        text,
    )


def normalize_text(text: str) -> str:
    """Склеивает разрядку старого набора в обычную строку."""
    cleaned = text.translate(_LIGATURES).replace("\u00a0", " ")
    cleaned = _APOSTROPHE.sub("'", cleaned)
    cleaned = _BEFORE_PUNCT.sub(r"\1", cleaned)
    cleaned = _AFTER_QUOTE.sub(r"\1", cleaned)
    cleaned = _collapse_letterspacing(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def join_line_texts(parts: list[str]) -> str:
    """Собирает абзац и снимает переносы на конце строки."""
    joined: list[str] = []
    for raw in parts:
        piece = re.sub(r"\s+", " ", _collapse_letterspacing(raw)).strip()
        if not piece:
            continue
        if joined and joined[-1].endswith("-") and piece[:1].islower():
            joined[-1] = joined[-1][:-1] + piece
        else:
            joined.append(piece)
    return normalize_text(" ".join(joined))


def _walk_lines(container):
    from pdfminer.layout import LTTextLine

    if isinstance(container, LTTextLine):
        yield container
        return
    if isinstance(container, (str, bytes)):
        return
    try:
        children = iter(container)
    except TypeError:
        return
    for child in children:
        yield from _walk_lines(child)


def _chars_to_text(chars) -> str:
    """Пробел слова не путает с разрядкой: зазор букв около 1 pt, между словами около 4 pt."""
    pieces: list[str] = []
    previous = None
    for char in chars:
        glyph = char.get_text()
        if previous is not None and glyph and not glyph.isspace():
            gap = float(char.x0) - float(previous.x1)
            size = float(getattr(previous, "size", 0) or getattr(char, "size", 0) or 10)
            if gap > max(1.8, size * 0.22) and (not pieces or not pieces[-1].endswith(" ")):
                pieces.append(" ")
        pieces.append(glyph)
        previous = char
    return "".join(pieces).replace("\n", " ").strip()


def _line_from_layout(page: int, layout_line, page_width: float) -> GlyphLine | None:
    from pdfminer.layout import LTChar

    chars = [item for item in layout_line if isinstance(item, LTChar)]
    text = _chars_to_text(chars) if chars else layout_line.get_text().replace("\n", " ").strip()
    if not text or not chars:
        return None
    fonts = [char.fontname.split("+")[-1] for char in chars]
    font = max(set(fonts), key=fonts.count)
    baselines = [char.matrix[5] for char in chars]
    return GlyphLine(
        page=page,
        text=text,
        x0=float(layout_line.x0),
        y0=float(layout_line.y0),
        x1=float(layout_line.x1),
        y1=float(layout_line.y1),
        baseline=float(statistics.median(baselines)),
        size=float(statistics.median(char.size for char in chars)),
        font=font,
        page_width=page_width,
    )


def extract_lines(path: Path, pages: list[int]) -> list[GlyphLine]:
    from pdfminer.high_level import extract_pages

    selected = [page - 1 for page in pages]
    lines: list[GlyphLine] = []
    for index, layout in enumerate(extract_pages(str(path), page_numbers=selected)):
        page_no = pages[index]
        for layout_line in _walk_lines(layout):
            parsed = _line_from_layout(page_no, layout_line, float(layout.width))
            if parsed is not None:
                lines.append(parsed)
    return lines


def _is_signature(line: GlyphLine) -> bool:
    """Сигнатура печатного листа: мелкая буква с номером, не текст книги."""
    if line.size >= 11.0:
        return False
    compact = re.sub(r"\s+", "", line.text)
    return re.fullmatch(r"[A-Z]\d{0,3}", compact) is not None


def is_translatable(line: GlyphLine, scope: str) -> bool:
    if _is_signature(line):
        return False
    if scope == "all":
        return True
    if any(marker in line.font for marker in _CHROME_FONTS):
        return False
    if _BOOK_FONT not in line.font:
        return False
    compact = re.sub(r"\s+", "", line.text)
    return not compact.isdigit()


def _column(line: GlyphLine) -> int:
    return 0 if line.center < line.page_width / 2 else 1


def _merge_fragments(lines: list[GlyphLine]) -> list[GlyphLine]:
    ordered = sorted(lines, key=lambda item: (-item.baseline, item.x0))
    merged: list[GlyphLine] = []
    for line in ordered:
        if not merged:
            merged.append(line)
            continue
        previous = merged[-1]
        same_row = abs(previous.baseline - line.baseline) < 1.4
        gap = line.x0 - previous.x1
        if same_row and -1.0 <= gap <= 16.0:
            text = f"{previous.text} {line.text}".strip()
            merged[-1] = GlyphLine(
                page=previous.page,
                text=text,
                x0=min(previous.x0, line.x0),
                y0=min(previous.y0, line.y0),
                x1=max(previous.x1, line.x1),
                y1=max(previous.y1, line.y1),
                baseline=(previous.baseline + line.baseline) / 2,
                size=max(previous.size, line.size),
                font=previous.font,
                page_width=previous.page_width,
            )
            continue
        merged.append(line)
    return merged


def _split_column(lines: list[GlyphLine]) -> list[list[GlyphLine]]:
    if not lines:
        return []
    body = [item for item in lines if item.size >= 11.5]
    column_left = min(item.x0 for item in (body or lines))
    gaps = [lines[i].baseline - lines[i + 1].baseline for i in range(len(lines) - 1)]
    ordinary = [gap for gap in gaps if 0 < gap < 30]
    median_gap = statistics.median(ordinary) if ordinary else 16.0
    groups: list[list[GlyphLine]] = [[lines[0]]]
    for index, line in enumerate(lines[1:], start=1):
        previous = lines[index - 1]
        gap = previous.baseline - line.baseline
        indent = line.x0 - previous.x0
        both_indented = line.x0 > column_left + 8 and previous.x0 > column_left + 8
        size_break = (previous.size < 11.4) != (line.size < 11.4)
        new_paragraph = (
            gap > median_gap * 1.35
            or 8.0 <= indent <= 26.0
            or indent <= -8.0
            or both_indented
            or size_break
        )
        if new_paragraph:
            groups.append([line])
        else:
            groups[-1].append(line)
    return groups


def group_paragraphs(lines: list[GlyphLine]) -> list[Paragraph]:
    """Группирует строки в абзацы: колонка, абзацный отступ, крупный зазор."""
    by_page: dict[int, list[GlyphLine]] = {}
    for line in lines:
        by_page.setdefault(line.page, []).append(line)
    paragraphs: list[Paragraph] = []
    for page in sorted(by_page):
        page_lines = by_page[page]
        columns: dict[int, list[GlyphLine]] = {}
        for line in page_lines:
            columns.setdefault(_column(line), []).append(line)
        page_index = 0
        body_by_column: dict[int, list[GlyphLine]] = {}
        built: list[Paragraph] = []
        for column in sorted(columns):
            merged = _merge_fragments(columns[column])
            for group in _split_column(merged):
                kind = "header" if max(item.size for item in group) < 11.4 else "body"
                source = join_line_texts([item.text for item in group])
                if not source:
                    continue
                paragraph = Paragraph(page, page_index, group, source, kind)
                built.append(paragraph)
                page_index += 1
                if kind == "body":
                    body_by_column.setdefault(column, []).extend(group)
        for paragraph in built:
            if paragraph.kind == "header":
                paragraph.measure_right = max(item.x1 for item in paragraph.lines)
            else:
                column = _column(paragraph.lines[0])
                mates = body_by_column.get(column) or paragraph.lines
                paragraph.measure_right = max(item.x1 for item in mates)
            paragraph.line_rights = [paragraph.measure_right] * len(paragraph.lines)
        paragraphs.extend(built)
    return _stitch_continuations(paragraphs)


def extract_paragraphs(path: Path, pages: list[int], scope: str = "book") -> list[Paragraph]:
    lines = [line for line in extract_lines(path, pages) if is_translatable(line, scope)]
    return group_paragraphs(lines)


def _text_width(text: str, font: str, size: float) -> float:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    return stringWidth(text, font, size) if text else 0.0


def fit_words(text: str, widths: list[float], font: str, size: float) -> list[str] | None:
    """Раскладывает слова по готовым ширинам строк. None, если не влезло."""
    words = text.split()
    if not words:
        return [""] * len(widths)
    lines: list[str] = []
    cursor = 0
    for width in widths:
        if cursor >= len(words):
            lines.append("")
            continue
        chosen: list[str] = []
        while cursor < len(words):
            trial = " ".join([*chosen, words[cursor]])
            if _text_width(trial, font, size) <= width + 0.6:
                chosen.append(words[cursor])
                cursor += 1
                continue
            break
        if not chosen:
            return None
        lines.append(" ".join(chosen))
    if cursor < len(words):
        return None
    return lines


def choose_layout(text: str, widths: list[float], font: str, start: float, floor: float) -> tuple[float, list[str]]:
    size = start
    while size >= floor - 0.01:
        fitted = fit_words(text, widths, font, round(size, 2))
        if fitted is not None:
            return round(size, 2), fitted
        size -= 0.25
    raise LayoutError("Текст не помещается в отведённые строки.")


def _register_font() -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        if not REGULAR_FONT.is_file():
            raise LayoutError(f"Нет файла шрифта: {REGULAR_FONT}")
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(REGULAR_FONT)))


def _slot_widths(paragraph: Paragraph) -> list[float]:
    widths: list[float] = []
    for index, line in enumerate(paragraph.lines):
        right = paragraph.measure_right
        if index < len(paragraph.line_rights):
            right = paragraph.line_rights[index]
        widths.append(max(12.0, right - line.x0))
    return widths


_SENTENCE_END = re.compile(r"""[.!?…]['"”’)\]]*$""")


def _ends_sentence(text: str) -> bool:
    return _SENTENCE_END.search(text.rstrip()) is not None


def _starts_lower(text: str) -> bool:
    for char in text.lstrip("«“\"'’([ "):
        if char.isalpha():
            return char.islower()
        return False
    return False


def _attach_rights(paragraph: Paragraph) -> None:
    if not paragraph.line_rights:
        paragraph.line_rights = [paragraph.measure_right] * len(paragraph.lines)


def _spread_header_partner(paragraphs: list[Paragraph], index: int) -> int | None:
    """Правая половина бегущего заголовка на той же базовой линии."""
    anchor = paragraphs[index]
    if anchor.kind != "header" or not anchor.lines or _ends_sentence(anchor.source):
        return None
    line = anchor.lines[0]
    if _column(line) != 0:
        return None
    best: int | None = None
    best_distance = 3.0
    for other_index, other in enumerate(paragraphs):
        if other_index == index or other.kind != "header" or not other.lines:
            continue
        mate = other.lines[0]
        if mate.page != line.page or _column(mate) != 1:
            continue
        distance = abs(mate.baseline - line.baseline)
        if distance <= 2.0 and distance < best_distance:
            best = other_index
            best_distance = distance
    return best


def _stitch_spread_headers(paragraphs: list[Paragraph]) -> list[Paragraph]:
    """Склеивает заголовок, который корешок разрезал на левую и правую половину."""
    used: set[int] = set()
    result: list[Paragraph] = []
    for index, paragraph in enumerate(paragraphs):
        if index in used:
            continue
        partner = _spread_header_partner(paragraphs, index)
        if partner is None:
            result.append(paragraph)
            continue
        used.add(partner)
        other = paragraphs[partner]
        left, right = (
            (paragraph, other) if paragraph.lines[0].x0 <= other.lines[0].x0 else (other, paragraph)
        )
        _attach_rights(left)
        _attach_rights(right)
        left.lines = [*left.lines, *right.lines]
        left.line_rights = [*left.line_rights, *right.line_rights]
        left.source = join_line_texts([line.text for line in left.lines])
        result.append(left)
    return result


def _column_lefts(paragraphs: list[Paragraph]) -> dict[tuple[int, int], float]:
    buckets: dict[tuple[int, int], list[float]] = {}
    for paragraph in paragraphs:
        if paragraph.kind != "body" or not paragraph.lines:
            continue
        line = paragraph.lines[0]
        buckets.setdefault((line.page, _column(line)), []).append(line.x0)
    return {key: min(values) for key, values in buckets.items()}


def _stitch_continuations(paragraphs: list[Paragraph]) -> list[Paragraph]:
    """Склеивает абзац, который оборвался на границе полосы.

    Строчная склейка работает и внутри страницы. Заглавная - только через
    страницу и только с левого края колонки, не с абзацного отступа.
    """
    paragraphs = _stitch_spread_headers(paragraphs)
    lefts = _column_lefts(paragraphs)
    kept: list[Paragraph] = []
    anchor: Paragraph | None = None
    for paragraph in paragraphs:
        _attach_rights(paragraph)
        if paragraph.kind != "body":
            kept.append(paragraph)
            continue
        line = paragraph.lines[0] if paragraph.lines else None
        at_edge = False
        cross_page = False
        if line is not None and anchor is not None and anchor.lines:
            edge = lefts.get((line.page, _column(line)), line.x0)
            at_edge = line.x0 <= edge + 3.0
            cross_page = anchor.lines[-1].page != line.page
        if (
            anchor is not None
            and not _ends_sentence(anchor.source)
            and (_starts_lower(paragraph.source) or (cross_page and at_edge))
        ):
            anchor.lines.extend(paragraph.lines)
            anchor.line_rights.extend(paragraph.line_rights)
            anchor.source = join_line_texts([line.text for line in anchor.lines])
            continue
        kept.append(paragraph)
        anchor = paragraph
    next_index: dict[int, int] = {}
    for paragraph in kept:
        page = paragraph.lines[0].page if paragraph.lines else paragraph.page
        paragraph.page = page
        paragraph.index = next_index.get(page, 0)
        next_index[page] = paragraph.index + 1
    return kept


def _header_limits(line: GlyphLine, kept: list[GlyphLine]) -> tuple[float, float]:
    """Ширина колонтитула: до номера страницы и до корешка, не до соседней полосы."""
    center = line.center
    gutter = line.page_width / 2
    left = 28.0
    right = line.page_width - 28.0
    if center < gutter:
        right = min(right, gutter - 16.0)
    else:
        left = max(left, gutter + 16.0)
    for obstacle in kept:
        if obstacle.page != line.page:
            continue
        if obstacle.y1 < line.y0 - 3 or obstacle.y0 > line.y1 + 3:
            continue
        if obstacle.center < center:
            left = max(left, obstacle.x1 + 8.0)
        else:
            right = min(right, obstacle.x0 - 8.0)
    half = max(12.0, min(center - left, right - center))
    return center - half, center + half


def _sample_paper(image) -> tuple[float, float, float]:
    """Медиана светлых малонасыщенных пикселей: цвет бумаги, без чернил и синей плашки."""
    pixels = image.load()
    step_x = max(1, image.width // 160)
    step_y = max(1, image.height // 120)
    samples: list[tuple[int, int, int]] = []
    for y in range(0, image.height, step_y):
        for x in range(0, image.width, step_x):
            red, green, blue = pixels[x, y][:3]
            if min(red, green, blue) > 205 and max(red, green, blue) - min(red, green, blue) < 16:
                samples.append((red, green, blue))
    if len(samples) < 30:
        return (224 / 255, 218 / 255, 220 / 255)
    return tuple(statistics.median(item[channel] for item in samples) / 255 for channel in range(3))


def _draw_fitted(canvas, text: str, x: float, baseline: float, width: float, size: float, mode: str) -> None:
    canvas.setFillColorRGB(0.08, 0.07, 0.06)
    canvas.setFont(FONT_NAME, size)
    if not text:
        return
    if mode == "center":
        canvas.drawCentredString(x + width / 2, baseline, text)
        return
    words = text.split(" ")
    raw = _text_width(text, FONT_NAME, size)
    extra = width - raw
    justify = mode == "justify" and len(words) > 1 and 0.8 < extra < width * 0.34
    if not justify:
        canvas.drawString(x, baseline, text)
        return
    gap = extra / (len(words) - 1)
    space = _text_width(" ", FONT_NAME, size)
    cursor = x
    for index, word in enumerate(words):
        canvas.drawString(cursor, baseline, word)
        cursor += _text_width(word, FONT_NAME, size)
        if index != len(words) - 1:
            cursor += space + gap


def _cover(canvas, x: float, y: float, width: float, height: float, paper: tuple[float, float, float]) -> None:
    if width <= 0 or height <= 0:
        return
    canvas.setFillColorRGB(*paper)
    canvas.rect(x, y, width, height, fill=1, stroke=0)


def _header_widths(paragraph: Paragraph, kept: list[GlyphLine]) -> list[float]:
    widths: list[float] = []
    for line in paragraph.lines:
        left, right = _header_limits(line, kept)
        widths.append(max(12.0, right - left))
    return widths


def _plan_paragraph(paragraph: Paragraph, translation: str, kept: list[GlyphLine]) -> tuple[float, list[str], float, float]:
    try:
        if paragraph.kind == "header":
            widths = _header_widths(paragraph, kept)
            size, lines = choose_layout(translation, widths, FONT_NAME, 11.0, 7.5)
            return size, lines, 0.0, 0.0
        widths = _slot_widths(paragraph)
        start = min(paragraph.lines[0].size, 12.0)
        size, lines = choose_layout(translation, widths, FONT_NAME, start, 7.5)
    except LayoutError as exc:
        preview = paragraph.source[:70]
        raise LayoutError(f"Стр. {paragraph.page}, абзац {paragraph.index}: {preview}") from exc
    return size, lines, 0.0, 0.0


def _markup(text: str) -> str:
    from xml.sax.saxutils import escape

    return escape(text).replace("\n", "<br/>")


def _append_overflow(writer, items: list[tuple[str, str]]) -> None:
    """Дописать в конец PDF абзацы, которые не вошли в свои строки."""
    import io

    from pypdf import PdfReader
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    buffer = io.BytesIO()
    body = ParagraphStyle(
        "dotlingo-overflow",
        fontName=FONT_NAME,
        fontSize=11,
        leading=15,
        alignment=TA_LEFT,
    )
    label = ParagraphStyle(
        "dotlingo-overflow-label",
        parent=body,
        fontSize=9,
        leading=12,
        spaceBefore=12,
    )
    story = [Paragraph(_markup("Текст, который не вошёл в полосу"), body)]
    for title, text in items:
        story.append(Paragraph(_markup(title), label))
        story.append(Spacer(1, 4))
        story.append(Paragraph(_markup(text or " "), body))
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=54,
        rightMargin=54,
        topMargin=56,
        bottomMargin=56,
    )
    try:
        document.build(story)
    except Exception as exc:
        raise LayoutError("Не удалось дописать текст, который не вошёл в полосу.") from exc
    buffer.seek(0)
    for page in PdfReader(buffer).pages:
        writer.add_page(page)


def _paint_header_line(
    canvas,
    line: GlyphLine,
    value: str,
    size: float,
    kept: list[GlyphLine],
    paper: tuple[float, float, float],
    *,
    cover: bool,
) -> None:
    """Закрывает половину бегущего заголовка и, если слот не пуст, пишет её перевод."""
    left, right = _header_limits(line, kept)
    text_width = _text_width(value, FONT_NAME, size) if value else 0.0
    if text_width <= 0:
        origin = left
    else:
        origin = min(max(line.center - text_width / 2, left), max(left, right - text_width))
    if cover:
        pad_y = 1.0
        cover_left = min(line.x0, origin) - 1.0
        cover_right = max(line.x1, origin + text_width) + 1.0
        _cover(
            canvas,
            cover_left,
            line.y0 - pad_y,
            cover_right - cover_left,
            line.y1 - line.y0 + pad_y * 2,
            paper,
        )
        return
    if value:
        _draw_fitted(canvas, value, origin, line.baseline, max(text_width, 1.0), size, "left")


def _paint_page(
    canvas,
    paragraphs: list[Paragraph],
    translations: dict[tuple[int, int], str],
    kept: list[GlyphLine],
    paper: tuple[float, float, float],
    page_no: int,
) -> list[tuple[str, str]]:
    """Рисует строки этой страницы. Абзац на две полосы планируется один раз."""
    overflow: list[tuple[str, str]] = []
    ready: list[Paragraph] = []
    for paragraph in paragraphs:
        text = translations.get(paragraph.key)
        if text is None:
            preview = paragraph.source[:80]
            raise LayoutError(f"Нет перевода для стр. {paragraph.page}, абзац {paragraph.index}: {preview}")
        if not paragraph.planned:
            paragraph.planned = True
            normalized = normalize_text(text)
            try:
                size, lines, _left, _right = _plan_paragraph(paragraph, normalized, kept)
            except LayoutError:
                title = f"Стр. {paragraph.page}. {paragraph.source[:70]}".strip()
                overflow.append((title, normalized))
                continue
            paragraph.fitted_size = size
            paragraph.fitted_lines = lines
        if not paragraph.fitted_lines:
            continue
        ready.append(paragraph)

    for paragraph in ready:
        lines = paragraph.fitted_lines
        size = paragraph.fitted_size
        if paragraph.kind == "header":
            for index, line in enumerate(paragraph.lines):
                if line.page != page_no:
                    continue
                value = lines[index] if index < len(lines) else ""
                _paint_header_line(canvas, line, value, size, kept, paper, cover=True)
            continue
        for line in paragraph.lines:
            if line.page != page_no:
                continue
            _cover(
                canvas,
                line.x0 - 0.8,
                line.y0 - 1.0,
                line.x1 - line.x0 + 1.6,
                line.y1 - line.y0 + 2.0,
                paper,
            )

    for paragraph in ready:
        lines = paragraph.fitted_lines
        size = paragraph.fitted_size
        if paragraph.kind == "header":
            for index, line in enumerate(paragraph.lines):
                if line.page != page_no:
                    continue
                value = lines[index] if index < len(lines) else ""
                _paint_header_line(canvas, line, value, size, kept, paper, cover=False)
            continue
        widths = _slot_widths(paragraph)
        last = max((index for index, value in enumerate(lines) if value), default=0)
        for index, (line, value) in enumerate(zip(paragraph.lines, lines, strict=True)):
            if line.page != page_no:
                continue
            mode = "justify" if index != last and value else "left"
            _draw_fitted(canvas, value, line.x0, line.baseline, widths[index], size, mode)
    return overflow


def _open_reader(path: Path):
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        opened = reader.decrypt("")
        if opened == 0:
            raise LayoutError("PDF закрыт паролем. Для укладки нужен файл без пароля пользователя.")
    return reader


def place_translations(
    source: Path,
    destination: Path,
    translations: dict[tuple[int, int], str],
    *,
    pages: list[int],
    scope: str = "book",
) -> list[Paragraph]:
    """Собирает PDF из выбранных страниц, подменяя текст переводом."""
    import io

    import pypdfium2 as pdfium
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas

    if scope not in {"book", "all"}:
        raise LayoutError("Режим укладки: book или all.")
    _register_font()
    source = Path(source)
    paragraphs = extract_paragraphs(source, pages, scope)
    kept = [line for line in extract_lines(source, pages) if not is_translatable(line, scope)]
    reader = _open_reader(source)
    document = pdfium.PdfDocument(str(source))
    overlay_buffer = io.BytesIO()
    first = reader.pages[pages[0] - 1]
    width = float(first.mediabox.width)
    height = float(first.mediabox.height)
    overlay = canvas.Canvas(overlay_buffer, pagesize=(width, height))
    by_page: dict[int, list[Paragraph]] = {page: [] for page in pages}
    for item in paragraphs:
        targets = {line.page for line in item.lines} or {item.page}
        for page in targets:
            if page in by_page:
                by_page[page].append(item)
    overflow: list[tuple[str, str]] = []
    for page_no in pages:
        bitmap = document[page_no - 1].render(scale=1)
        paper = _sample_paper(bitmap.to_pil())
        overflow.extend(_paint_page(overlay, by_page[page_no], translations, kept, paper, page_no))
        overlay.showPage()
    overlay.save()
    document.close()
    overlay_buffer.seek(0)
    stamps = PdfReader(overlay_buffer)
    writer = PdfWriter()
    for index, page_no in enumerate(pages):
        # Страница должна уже лежать в writer, иначе pypdf 6 теряет шрифт наложения.
        writer.add_page(reader.pages[page_no - 1])
        writer.pages[-1].merge_page(stamps.pages[index])
    if overflow:
        _append_overflow(writer, overflow)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        writer.write(handle)
    return paragraphs
