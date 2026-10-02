from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

# Акцентный монохром: тёмная пластина и светлая закрытая книга.
_BG = "#0B0B0E"
_INK = "#F5F5F7"


def _draw_icon(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), _BG)
    draw = ImageDraw.Draw(image)
    plate = max(2, round(size * 0.22))
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=plate, fill=_BG)
    left = round(size * 0.29)
    top = round(size * 0.20)
    right = round(size * 0.73)
    bottom = round(size * 0.80)
    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=max(2, round(size * 0.06)),
        fill=_INK,
    )
    spine_x = left + max(2, round(size * 0.055))
    inset = max(2, round(size * 0.07))
    draw.line(
        (spine_x, top + inset, spine_x, bottom - inset),
        fill=_BG,
        width=max(1, round(size * 0.035)),
    )
    return image


def main() -> None:
    output = Path(__file__).resolve().parents[1] / "src" / "dotlingo" / "assets" / "app_icon.ico"
    images = [_draw_icon(size) for size in (16, 24, 32, 48, 64, 128, 256)]
    output.parent.mkdir(parents=True, exist_ok=True)
    images[-1].save(
        output,
        format="ICO",
        sizes=[(im.width, im.height) for im in images],
        append_images=images[:-1],
    )


if __name__ == "__main__":
    main()
