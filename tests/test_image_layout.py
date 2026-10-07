from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from dotlingo.image_layout import (
    OcrLine,
    group_ocr_lines,
    lines_outside_text_layer,
    overlaps_box,
    paint_lines,
    paragraph_text,
)
from dotlingo.pdf_layout import REGULAR_FONT


def test_group_joins_a_hyphenated_line_and_keeps_the_page_number() -> None:
    groups = group_ocr_lines(
        [
            OcrLine(0, "Hel-", 10, 10, 80, 30),
            OcrLine(1, "lo there", 10, 34, 140, 54),
            OcrLine(2, "12", 180, 200, 210, 220),
        ]
    )
    assert [paragraph_text(group) for group in groups] == ["Hello there", "12"]


def test_overlap_keeps_a_separate_caption() -> None:
    body = (10.0, 10.0, 200.0, 40.0)
    same = OcrLine(0, "Alice", 12, 12, 180, 36)
    caption = OcrLine(1, "Plate", 10, 80, 90, 110)
    assert overlaps_box(same.box, body)
    kept = lines_outside_text_layer([same, caption], [body])
    assert [line.index for line in kept] == [1]


def test_paint_removes_ink_and_keeps_the_neighbourhood() -> None:
    image = Image.new("RGB", (420, 160), (230, 222, 206))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(REGULAR_FONT), 36)
    draw.text((24, 50), "Tea party", font=font, fill=(20, 20, 20))
    ink = next(
        (x, y)
        for y in range(52, 92)
        for x in range(24, 210)
        if image.getpixel((x, y)) == (20, 20, 20)
    )
    untouched = image.getpixel((8, 8))
    line = OcrLine(0, "Tea party", 24, 52, 210, 92)
    painted = paint_lines(image, [line], {0: "Чай"})
    assert painted.getpixel((8, 8)) == untouched
    assert painted.getpixel(ink) != (20, 20, 20)
    assert image.getpixel(ink) == (20, 20, 20)
