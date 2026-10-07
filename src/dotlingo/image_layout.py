"""Текст на фотографии и в тех местах PDF, где его нельзя выделить.

Распознавание делает системный Windows.Media.Ocr (пакет winocr): на этой
машине уже есть английский и русский, отдельный Tesseract не нужен.
Буквы закрашиваются цветом фона вокруг рамки, перевод набирается Old Standard.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from dotlingo.pdf_layout import REGULAR_FONT

_PAD = 2


class OcrUnavailable(Exception):
    """На машине нет Windows OCR или пакета winocr."""


@dataclass(frozen=True)
class OcrLine:
    index: int
    text: str
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return max(1, self.x1 - self.x0)

    @property
    def height(self) -> int:
        return max(1, self.y1 - self.y0)

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (self.x0, self.y0, self.x1, self.y1)


def resolve_ocr_language(requested: str | None) -> str:
    """Язык Windows OCR. Пустое значение и auto берут язык профиля Windows."""
    code = (requested or "").strip().replace("_", "-")
    try:
        from winrt.windows.media.ocr import OcrEngine
    except ImportError as exc:
        raise OcrUnavailable("Для сканов нужен пакет winocr: pip install winocr") from exc
    available = [item.language_tag for item in OcrEngine.available_recognizer_languages]
    if not available:
        raise OcrUnavailable(
            "В Windows нет языка распознавания. Добавьте английский или русский в параметрах языка."
        )
    if not code or code.lower() == "auto":
        engine = OcrEngine.try_create_from_user_profile_languages()
        tag = ""
        if engine is not None and engine.recognizer_language is not None:
            tag = str(engine.recognizer_language.language_tag or "")
        return tag or available[0]
    lowered = {tag.lower(): tag for tag in available}
    candidates = [code, code.split("-")[0]]
    for candidate in candidates:
        found = lowered.get(candidate.lower())
        if found:
            return found
        for tag in available:
            if tag.lower().startswith(candidate.lower() + "-"):
                return tag
    shown = ", ".join(available)
    raise OcrUnavailable(f"Windows не распознаёт язык {code}. Доступны: {shown}.")


def ocr_image(image: Image.Image, lang: str = "en") -> list[OcrLine]:
    """Строки и рамки. Начало координат слева сверху, как у картинки."""
    try:
        from winocr import recognize_pil_sync
    except ImportError as exc:
        raise OcrUnavailable("Для снимков нужен пакет winocr: pip install winocr") from exc
    rgb = image.convert("RGB")
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine

        if not OcrEngine.is_language_supported(Language(lang)):
            raise OcrUnavailable(f"Windows OCR не принял язык {lang}.")
        result = recognize_pil_sync(rgb, lang)
    except OcrUnavailable:
        raise
    except Exception as exc:
        raise OcrUnavailable(f"Windows OCR не принял язык {lang}.") from exc
    lines: list[OcrLine] = []
    for raw in result.get("lines") or []:
        words = raw.get("words") or []
        rects = [word["bounding_rect"] for word in words if word.get("bounding_rect")]
        text = str(raw.get("text") or "").strip()
        if not text or not rects:
            continue
        x0 = int(min(rect["x"] for rect in rects))
        y0 = int(min(rect["y"] for rect in rects))
        x1 = int(max(rect["x"] + rect["width"] for rect in rects) + 0.5)
        y1 = int(max(rect["y"] + rect["height"] for rect in rects) + 0.5)
        x0 = max(0, min(x0, rgb.width - 1))
        y0 = max(0, min(y0, rgb.height - 1))
        x1 = max(x0 + 1, min(rgb.width, x1))
        y1 = max(y0 + 1, min(rgb.height, y1))
        lines.append(OcrLine(len(lines), text, x0, y0, x1, y1))
    return lines


def overlaps_box(left: tuple[float, float, float, float], right: tuple[float, float, float, float], pad: float = 6) -> bool:
    """Правда, если рамки делят заметную часть первой."""
    ax0, ay0, ax1, ay1 = left
    bx0, by0, bx1, by1 = right
    bx0 -= pad
    by0 -= pad
    bx1 += pad
    by1 += pad
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return False
    area = max(1.0, (ax1 - ax0) * (ay1 - ay0))
    return (ix1 - ix0) * (iy1 - iy0) / area > 0.35


def lines_outside_text_layer(lines: list[OcrLine], text_boxes: list[tuple[float, float, float, float]]) -> list[OcrLine]:
    """Оставляет только то, чего нет в выделяемом слое PDF."""
    kept: list[OcrLine] = []
    for line in lines:
        if any(overlaps_box(line.box, box) for box in text_boxes):
            continue
        kept.append(line)
    return kept


def _luma(pixel: tuple[int, int, int]) -> float:
    return (pixel[0] + pixel[1] + pixel[2]) / 3


def _median_color(samples: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    return tuple(int(statistics.median(pixel[channel] for pixel in samples)) for channel in range(3))


def _ink_color(image: Image.Image, line: OcrLine) -> tuple[int, int, int]:
    pixels = image.load()
    ring: list[tuple[int, int, int]] = []
    ink: list[tuple[int, int, int]] = []
    width, height = image.size
    for x in range(max(0, line.x0 - 6), min(width, line.x1 + 6)):
        for y in (line.y0 - 4, line.y0 - 3, line.y1 + 3, line.y1 + 4):
            if 0 <= y < height:
                ring.append(pixels[x, y])
    paper = _median_color(ring) if ring else (240, 240, 240)
    for y in range(line.y0, line.y1):
        for x in range(line.x0, line.x1):
            pixel = pixels[x, y]
            if abs(_luma(pixel) - _luma(paper)) > 35:
                ink.append(pixel)
    if not ink:
        return (25, 25, 25) if _luma(paper) > 140 else (245, 245, 245)
    return _median_color(ink)


def _first_pixel(outside, x: int, rows: range) -> tuple[int, int, int] | None:
    for y in rows:
        pixel = outside(x, y)
        if pixel is not None:
            return pixel
    return None


def _erase_lines(image: Image.Image, lines: list[OcrLine]) -> None:
    """Закрывает рамки. Ровный фон заливается медианой, пёстрый склеивается сверху и снизу."""
    if not lines:
        return
    mask = Image.new("1", image.size, 0)
    draw = ImageDraw.Draw(mask)
    for line in lines:
        draw.rectangle(
            (line.x0 - _PAD, line.y0 - _PAD, line.x1 + _PAD, line.y1 + _PAD),
            fill=1,
        )
    covered = mask.load()
    pixels = image.load()
    width, height = image.size

    def outside(x: int, y: int) -> tuple[int, int, int] | None:
        if 0 <= x < width and 0 <= y < height and not covered[x, y]:
            return pixels[x, y]
        return None

    for line in lines:
        x0 = max(0, line.x0 - _PAD)
        y0 = max(0, line.y0 - _PAD)
        x1 = min(width, line.x1 + _PAD)
        y1 = min(height, line.y1 + _PAD)
        samples: list[tuple[int, int, int]] = []
        for x in range(x0, x1):
            for y in range(y0 - 8, y0):
                pixel = outside(x, y)
                if pixel is not None:
                    samples.append(pixel)
            for y in range(y1 + 1, y1 + 9):
                pixel = outside(x, y)
                if pixel is not None:
                    samples.append(pixel)
        if not samples:
            continue
        lums = [_luma(pixel) for pixel in samples]
        mean = sum(lums) / len(lums)
        variance = sum((value - mean) ** 2 for value in lums) / len(lums)
        if variance < 90:
            color = _median_color(samples)
            for y in range(y0, y1):
                for x in range(x0, x1):
                    pixels[x, y] = color
            continue
        for x in range(x0, x1):
            top = _first_pixel(outside, x, range(y0 - 1, max(-1, y0 - 28), -1))
            bottom = _first_pixel(outside, x, range(y1, min(height, y1 + 28)))
            if top is None and bottom is None:
                continue
            top = top or bottom
            bottom = bottom or top
            assert top is not None and bottom is not None
            span = max(1, y1 - y0 - 1)
            for y in range(y0, y1):
                blend = (y - y0) / span
                pixels[x, y] = tuple(int(top[channel] * (1 - blend) + bottom[channel] * blend) for channel in range(3))


def _fit_font(text: str, width: int, height: int) -> ImageFont.FreeTypeFont:
    if not REGULAR_FONT.is_file():
        raise OcrUnavailable(f"Нет шрифта {REGULAR_FONT}")
    low, high = 8, max(8, int(height * 0.9))
    best = ImageFont.truetype(str(REGULAR_FONT), low)
    while low <= high:
        size = (low + high) // 2
        font = ImageFont.truetype(str(REGULAR_FONT), size)
        if font.getlength(text) <= max(4, width - 4):
            best = font
            low = size + 1
        else:
            high = size - 1
    return best


def paint_lines(image: Image.Image, lines: list[OcrLine], translations: dict[int, str]) -> Image.Image:
    """Стирает выбранные строки и пишет перевод в те же рамки."""
    chosen = [line for line in lines if translations.get(line.index, "").strip()]
    canvas = image.convert("RGB")
    if not chosen:
        return canvas
    inks = {line.index: _ink_color(canvas, line) for line in chosen}
    _erase_lines(canvas, chosen)
    draw = ImageDraw.Draw(canvas)
    for line in chosen:
        text = translations[line.index].strip()
        font = _fit_font(text, line.width, line.height)
        bbox = font.getbbox(text)
        text_height = bbox[3] - bbox[1]
        x = line.x0 - bbox[0]
        y = line.y0 + max(0, (line.height - text_height) / 2) - bbox[1]
        draw.text((x, y), text, font=font, fill=inks[line.index])
    return canvas


def replace_image_text(image: Image.Image, translations: dict[int, str], *, lang: str = "en") -> tuple[Image.Image, list[OcrLine]]:
    found = ocr_image(image, lang)
    return paint_lines(image, found, translations), found


def _digits_only(text: str) -> bool:
    compact = "".join(text.split())
    return bool(compact) and compact.isdigit()


def _new_paragraph(previous: OcrLine, line: OcrLine) -> bool:
    """Новый абзац, если строка далеко снизу или начинается в другой колонке."""
    gap = line.y0 - previous.y1
    tall = max(previous.height, line.height, 1)
    if gap > tall * 0.85 or gap < -(tall * 0.2):
        return True
    side_by_side = line.x0 >= previous.x1 - 4 and gap < tall * 0.35
    return side_by_side


def group_ocr_lines(lines: list[OcrLine]) -> list[list[OcrLine]]:
    """Соседние строки одного столбца склеиваются в абзац. Номер страницы остаётся отдельно."""
    ordered = sorted(lines, key=lambda item: (item.y0, item.x0))
    groups: list[list[OcrLine]] = []
    for line in ordered:
        if not line.text.strip():
            continue
        if (
            not groups
            or _digits_only(line.text)
            or _digits_only(groups[-1][-1].text)
            or _new_paragraph(groups[-1][-1], line)
        ):
            groups.append([line])
            continue
        groups[-1].append(line)
    return groups


def paragraph_text(lines: list[OcrLine]) -> str:
    """Текст абзаца. Перенос с дефисом на строке склеивается, если следующая начинается со строчной."""
    result = ""
    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        if not result:
            result = text
            continue
        if result.endswith("-") and text[0].isalpha() and text[0].islower():
            result = f"{result[:-1]}{text}"
        else:
            result = f"{result} {text}"
    return result


def _wrap_words(text: str, widths: list[int], font: ImageFont.FreeTypeFont) -> list[str] | None:
    words = text.split()
    if not words:
        return [""] * len(widths)
    placed: list[str] = []
    index = 0
    for width in widths:
        limit = max(4, width - 4)
        chosen: list[str] = []
        while index < len(words):
            trial = " ".join([*chosen, words[index]])
            if font.getlength(trial) <= limit:
                chosen.append(words[index])
                index += 1
                continue
            break
        if not chosen:
            return None
        placed.append(" ".join(chosen))
    if index < len(words):
        return None
    placed.extend("" for _ in range(len(widths) - len(placed)))
    return placed


def _wrap_loose(text: str, widths: list[int], font: ImageFont.FreeTypeFont) -> tuple[list[str], str]:
    words = text.split()
    placed: list[str] = []
    index = 0
    for width in widths:
        limit = max(4, width - 4)
        chosen: list[str] = []
        while index < len(words):
            word = words[index]
            trial = word if not chosen else " ".join([*chosen, word])
            if chosen and font.getlength(trial) > limit:
                break
            chosen.append(word)
            index += 1
            if font.getlength(" ".join(chosen)) > limit:
                break
        placed.append(" ".join(chosen))
    return placed, " ".join(words[index:])


def _fit_group(text: str, lines: list[OcrLine]) -> tuple[list[str], str, ImageFont.FreeTypeFont]:
    if not REGULAR_FONT.is_file():
        raise OcrUnavailable(f"Нет шрифта {REGULAR_FONT}")
    heights = [line.height for line in lines] or [12]
    high = max(8, int(min(heights) * 0.92))
    best = ImageFont.truetype(str(REGULAR_FONT), 8)
    widths = [line.width for line in lines]
    for size in range(high, 7, -1):
        font = ImageFont.truetype(str(REGULAR_FONT), size)
        wrapped = _wrap_words(text, widths, font)
        if wrapped is not None:
            return wrapped, "", font
    wrapped, leftover = _wrap_loose(text, widths, best)
    return wrapped, leftover, best


def _draw_in_box(
    draw: ImageDraw.ImageDraw,
    line: OcrLine,
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
) -> None:
    if not text:
        return
    bbox = font.getbbox(text)
    text_height = bbox[3] - bbox[1]
    x = line.x0 - bbox[0]
    y = line.y0 + max(0, (line.height - text_height) / 2) - bbox[1]
    draw.text((x, y), text, font=font, fill=fill)


def _erase_flat(image: Image.Image, lines: list[OcrLine]) -> None:
    """Закрывает рамку ровным цветом бумаги. Так штрихи скана не остаются под переводом."""
    if not lines:
        return
    pixels = image.load()
    width, height = image.size
    for line in lines:
        pad = max(6, int(line.height * 0.22))
        x0 = max(0, line.x0 - pad)
        y0 = max(0, line.y0 - pad)
        x1 = min(width, line.x1 + pad)
        y1 = min(height, line.y1 + pad)
        samples: list[tuple[int, int, int]] = []
        for x in range(x0, x1):
            for y in (y0 - 3, y0 - 2, y1 + 2, y1 + 3):
                if 0 <= y < height:
                    samples.append(pixels[x, y])
        color = _median_color(samples) if samples else (250, 250, 250)
        for y in range(y0, y1):
            for x in range(x0, x1):
                pixels[x, y] = color


def place_scan_groups(
    image: Image.Image,
    groups: list[list[OcrLine]],
    translations: dict[int, str],
) -> tuple[Image.Image, str]:
    """Стирает строки абзаца и пишет перевод в те же рамки. Хвост, который не вошёл, возвращается."""
    canvas = image.convert("RGB")
    chosen: list[tuple[int, list[OcrLine], str]] = []
    for index, group in enumerate(groups):
        text = translations.get(index, "").strip()
        if text and group:
            chosen.append((index, group, text))
    if not chosen:
        return canvas, ""
    inks = {
        id(line): _ink_color(canvas, line)
        for _index, group, _text in chosen
        for line in group
    }
    _erase_flat(canvas, [line for _index, group, _text in chosen for line in group])
    draw = ImageDraw.Draw(canvas)
    overflow: list[str] = []
    for _index, group, text in chosen:
        wrapped, leftover, font = _fit_group(text, group)
        for line, piece in zip(group, wrapped, strict=False):
            _draw_in_box(draw, line, piece, font, inks[id(line)])
        if leftover:
            overflow.append(leftover)
    return canvas, "\n\n".join(overflow)


def render_pdf_page(path: Path, page: int, scale: float = 2.0) -> tuple[Image.Image, float, float]:
    """Страница PDF как картинка. Второе и третье число: ширина и высота в пунктах."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path))
    try:
        pdf_page = document[page - 1]
        width, height = pdf_page.get_size()
        image = pdf_page.render(scale=scale).to_pil().convert("RGB")
    finally:
        document.close()
    return image, float(width), float(height)


def page_sizes(path: Path) -> list[tuple[float, float]]:
    """Ширина и высота каждой страницы в пунктах, с учётом поворота."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path))
    try:
        sizes: list[tuple[float, float]] = []
        for index in range(len(document)):
            width, height = document[index].get_size()
            sizes.append((float(width), float(height)))
        return sizes
    finally:
        document.close()


def text_layer_lines(path: Path, page: int, scale: float, page_height: float) -> list[OcrLine]:
    """Строки выделяемого слоя в пикселях картинки того же масштаба, что и скан."""
    from dotlingo.pdf_layout import extract_lines

    lines: list[OcrLine] = []
    for item in extract_lines(path, [page]):
        x0 = int(item.x0 * scale)
        y0 = int((page_height - item.y1) * scale)
        x1 = max(x0 + 1, int(item.x1 * scale))
        y1 = max(y0 + 1, int((page_height - item.y0) * scale))
        lines.append(OcrLine(len(lines), item.text, x0, y0, x1, y1))
    return lines


def text_layer_pixel_boxes(path: Path, page: int, scale: float, page_height: float) -> list[tuple[float, float, float, float]]:
    """Рамки выделяемого текста в пикселях картинки (начало слева сверху)."""
    from dotlingo.pdf_layout import extract_lines

    boxes: list[tuple[float, float, float, float]] = []
    for line in extract_lines(path, [page]):
        boxes.append(
            (
                line.x0 * scale,
                (page_height - line.y1) * scale,
                line.x1 * scale,
                (page_height - line.y0) * scale,
            )
        )
    return boxes
