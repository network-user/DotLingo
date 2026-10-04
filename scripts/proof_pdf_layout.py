"""Проба укладки: два разворота «Алисы» с русским текстом книги.

Перевод в alice_proof_ru.json подставлен вручную, чтобы проверить полосы,
шрифт и иллюстрацию без локальной модели. Тот же place_translations
принимает словарь из движка DotLingo.

    python scripts/proof_pdf_layout.py
    python scripts/proof_pdf_layout.py --scope all
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotlingo.pdf_layout import place_translations  # noqa: E402


def _load(path: Path) -> dict[tuple[int, int], str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    translations: dict[tuple[int, int], str] = {}
    for page, items in raw.items():
        for index, text in items.items():
            translations[(int(page), int(index))] = text
    return translations


def main() -> None:
    parser = argparse.ArgumentParser(description="Проба укладки перевода в PDF")
    parser.add_argument("--pdf", type=Path, default=Path(r"C:\Users\User\Downloads\Alice_in_Wonderland.pdf"))
    parser.add_argument("--pages", default="11,53")
    parser.add_argument("--translations", type=Path, default=Path(__file__).with_name("alice_proof_ru.json"))
    parser.add_argument("--out", type=Path, default=Path(r"C:\Users\User\Downloads\Alice_layout_proof.pdf"))
    parser.add_argument("--scope", choices=("book", "all"), default="book")
    args = parser.parse_args()
    pages = [int(item) for item in args.pages.split(",") if item.strip()]
    if not args.pdf.is_file():
        raise SystemExit(f"Нет файла: {args.pdf}")
    paragraphs = place_translations(
        args.pdf,
        args.out,
        _load(args.translations),
        pages=pages,
        scope=args.scope,
    )
    for item in paragraphs:
        print(f"стр. {item.page} абзац {item.index} {item.kind} {item.fitted_size:.2f} пт, строк {len(item.lines)}")
    print(args.out)


if __name__ == "__main__":
    main()
