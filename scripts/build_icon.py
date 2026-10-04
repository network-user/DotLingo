from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

# Акцентный монохром: тёмная пластина и светлый открытый разворот.
# Те же кривые, что в app_icon.svg и у .brand__mark.
_BG = "#0B0B0E"
_INK = "#F5F5F7"


def _quad(start, control, end, steps: int = 18) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for step in range(steps + 1):
        t = step / steps
        u = 1 - t
        points.append(
            (
                u * u * start[0] + 2 * u * t * control[0] + t * t * end[0],
                u * u * start[1] + 2 * u * t * control[1] + t * t * end[1],
            )
        )
    return points


def _page(unit, mirror: bool = False) -> list[tuple[float, float]]:
    def point(x: float, y: float) -> tuple[float, float]:
        return (unit(16 - x if mirror else x), unit(y))

    top = _quad(point(2.15, 4.35), point(4.55, 3.55), point(7.05, 5.85))
    bottom = _quad(point(7.05, 11.55), point(4.45, 11.35), point(2.15, 12.35))
    return top + bottom[1:]


def _render(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), _BG)
    draw = ImageDraw.Draw(image)

    def unit(value: float) -> float:
        return size * value / 16

    plate = max(2, round(unit(3.5)))
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=plate, fill=_BG)
    draw.polygon(_page(unit), fill=_INK)
    draw.polygon(_page(unit, mirror=True), fill=_INK)
    return image


def _draw_icon(size: int) -> Image.Image:
    # Мелкий изгиб в Pillow ломается. Рисуем крупнее и уменьшаем.
    if size >= 128:
        return _render(size)
    image = _render(size * 8)
    return image.resize((size, size), Image.Resampling.LANCZOS)


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
