"""Текст с фотографии или с невыделяемого места PDF.

Windows OCR читает строки и рамки. Перевод из JSON подставляется в те же рамки.
Ключ JSON - номер строки из --list.

    python scripts/proof_image_layout.py --demo
    python scripts/proof_image_layout.py --image photo.jpg --list
    python scripts/proof_image_layout.py --image photo.jpg --translations ru.json --out out.png
    python scripts/proof_image_layout.py --pdf book.pdf --page 2 --gaps-only --list
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotlingo.image_layout import (  # noqa: E402
    lines_outside_text_layer,
    ocr_image,
    paint_lines,
    render_pdf_page,
    replace_image_text,
    text_layer_pixel_boxes,
)
from dotlingo.pdf_layout import REGULAR_FONT  # noqa: E402


def _load(path: Path) -> dict[int, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {int(key): value for key, value in raw.items()}


def _demo_photo() -> Image.Image:
    image = Image.new("RGB", (1100, 720), (186, 214, 232))
    pixels = image.load()
    for y in range(720):
        for x in range(1100):
            shade = int(170 + (y / 720) * 50)
            pixels[x, y] = (shade - 20, shade + 10, shade + 28)
    draw = ImageDraw.Draw(image)
    draw.ellipse((80, 380, 1020, 980), fill=(78, 122, 70))
    draw.rounded_rectangle((260, 150, 840, 430), radius=18, fill=(214, 186, 132))
    face = ImageFont.truetype(str(REGULAR_FONT), 54)
    small = ImageFont.truetype(str(REGULAR_FONT), 40)
    draw.text((300, 190), "Tea party this way", font=face, fill=(48, 32, 18))
    draw.text((300, 290), "Mad Hatter", font=small, fill=(48, 32, 18))
    return image


def _print_lines(lines) -> None:
    if not lines:
        print("Строк нет.")
        return
    for line in lines:
        print(f"{line.index:3}  {line.x0},{line.y0} {line.width}x{line.height}  {line.text}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Замена текста на фотографии или в невыделяемом PDF")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--scale", type=float, default=2.0)
    parser.add_argument("--lang", default="en")
    parser.add_argument("--gaps-only", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--translations", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    if args.demo:
        photo = _demo_photo()
        out = args.out or Path(r"C:\Users\User\Downloads\DotLingo_photo_proof.png")
        result, found = replace_image_text(
            photo,
            {0: "Чайная вечеринка", 1: "Безумный Шляпник"},
            lang="en",
        )
        _print_lines(found)
        out.parent.mkdir(parents=True, exist_ok=True)
        result.save(out)
        print(out)
        return

    if args.image is None and args.pdf is None:
        raise SystemExit("Нужен --image, --pdf или --demo.")
    if args.pdf is not None:
        if not args.pdf.is_file():
            raise SystemExit(f"Нет файла: {args.pdf}")
        image, _width, height = render_pdf_page(args.pdf, args.page, args.scale)
        found = ocr_image(image, args.lang)
        if args.gaps_only:
            boxes = text_layer_pixel_boxes(args.pdf, args.page, args.scale, height)
            found = lines_outside_text_layer(found, boxes)
    else:
        if args.image is None or not args.image.is_file():
            raise SystemExit(f"Нет файла: {args.image}")
        image = Image.open(args.image).convert("RGB")
        found = ocr_image(image, args.lang)

    if args.list or args.translations is None:
        _print_lines(found)
        if args.translations is None:
            return

    painted = paint_lines(image, found, _load(args.translations))
    out = args.out or Path(r"C:\Users\User\Downloads\DotLingo_photo_proof.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".pdf":
        painted.save(out, "PDF", resolution=144)
    else:
        painted.save(out)
    print(out)


if __name__ == "__main__":
    main()
