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
    radius = max(2, round(size * 0.05))
    draw.rounded_rectangle(
        (round(size * 0.34), round(size * 0.26), round(size * 0.78), round(size * 0.76)),
        radius=radius,
        fill=_INK,
    )
    cover = (round(size * 0.24), round(size * 0.20), round(size * 0.68), round(size * 0.80))
    draw.rounded_rectangle(cover, radius=max(2, round(size * 0.06)), fill=_INK)
    inset = max(2, round(size * 0.08))
    spine = cover[0] + max(2, round(size * 0.07))
    draw.line(
        (spine, cover[1] + inset, spine, cover[3] - inset),
        fill=_BG,
        width=max(1, round(size * 0.04)),
    )
    edge = max(1, round(size * 0.018))
    for step in (0.045, 0.075):
        x = cover[2] + round(size * step)
        draw.line((x, cover[1] + inset, x, cover[3] - inset), fill=_BG, width=edge)
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
