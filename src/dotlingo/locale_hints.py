"""Подсказки языка системы для нового проекта. Не угадывают качество перевода."""

from __future__ import annotations

import sys

from dotlingo.languages import LANGUAGES

# Первичный идентификатор Windows (младшие 10 бит LANGID) → код из каталога.
_PRIMARY: dict[int, str] = {
    0x01: "ar",
    0x05: "cs",
    0x06: "da",
    0x07: "de",
    0x08: "el",
    0x09: "en",
    0x0A: "es",
    0x0B: "fi",
    0x0C: "fr",
    0x0D: "he",
    0x0E: "hu",
    0x10: "it",
    0x11: "ja",
    0x12: "ko",
    0x13: "nl",
    0x14: "no",
    0x15: "pl",
    0x16: "pt",
    0x18: "ro",
    0x19: "ru",
    0x1D: "sv",
    0x1E: "th",
    0x1F: "tr",
    0x20: "ur",
    0x21: "id",
    0x22: "uk",
    0x29: "fa",
    0x2A: "vi",
    0x39: "hi",
    0x3E: "ms",
    0x3F: "kk",
    0x45: "bn",
}

# Упрощённый китайский. Остальные подъязыки китайского считаем традиционными.
_SIMPLIFIED_CHINESE = {0x02, 0x04}


def code_from_langid(lang_id: int) -> str:
    """Вернуть код каталога или пустую строку, если раскладка нам не знакома."""
    value = int(lang_id) & 0xFFFF
    primary = value & 0x3FF
    if primary == 0x04:
        sub = value >> 10
        code = "zh" if sub in _SIMPLIFIED_CHINESE else "zh-Hant"
    else:
        code = _PRIMARY.get(primary, "")
    return code if code in LANGUAGES else ""


def keyboard_language() -> str:
    """Язык текущей раскладки. Пустая строка вне Windows или при ошибке."""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes

        lang_id = ctypes.windll.user32.GetKeyboardLayout(0) & 0xFFFF
    except (AttributeError, OSError):
        return ""
    return code_from_langid(lang_id)


def interface_language() -> str:
    """Язык интерфейса Windows. Пустая строка вне Windows или при ошибке."""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes

        lang_id = ctypes.windll.user32.GetUserDefaultUILanguage() & 0xFFFF
    except (AttributeError, OSError):
        return ""
    return code_from_langid(lang_id)
