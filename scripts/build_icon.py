from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    output = Path(__file__).resolve().parents[1] / "src" / "dotlingo" / "assets" / "app_icon.ico"
    images = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        image = Image.new("RGBA", (size, size), "#11191d")
        draw = ImageDraw.Draw(image)
        scale = size / 256
        draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=round(size * .22), fill="#11191d")
        draw.rectangle((round(70 * scale), round(62 * scale), round(188 * scale), round(181 * scale)), fill="#e6eee9")
        draw.polygon([(0, round(94 * scale)), (round(70 * scale), round(62 * scale)), (round(70 * scale), round(181 * scale)), (0, size)], fill="#72d6c2")
        width = max(1, round(9 * scale))
        for y, x2 in ((91, 152), (118, 142), (145, 124)):
            draw.line((round(90 * scale), round(y * scale), round(x2 * scale), round(y * scale)), fill="#243235", width=width)
        r = round(20 * scale)
        x, y = round(183 * scale), round(171 * scale)
        draw.ellipse((x - r, y - r, x + r, y + r), fill="#e59c72")
        images.append(image)
    output.parent.mkdir(parents=True, exist_ok=True)
    images[-1].save(output, format="ICO", sizes=[(im.width, im.height) for im in images], append_images=images[:-1])


if __name__ == "__main__":
    main()
