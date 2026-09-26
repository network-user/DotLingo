"""Language labels and conservative model capability helpers for the desktop UI."""

from __future__ import annotations

from typing import Any

LANGUAGES: dict[str, str] = {
    "ar": "Арабский",
    "bn": "Бенгальский",
    "cs": "Чешский",
    "da": "Датский",
    "de": "Немецкий",
    "el": "Греческий",
    "en": "Английский",
    "es": "Испанский",
    "fa": "Персидский",
    "fi": "Финский",
    "fr": "Французский",
    "he": "Иврит",
    "hi": "Хинди",
    "hu": "Венгерский",
    "id": "Индонезийский",
    "it": "Итальянский",
    "ja": "Японский",
    "ko": "Корейский",
    "ms": "Малайский",
    "nl": "Нидерландский",
    "no": "Норвежский",
    "pl": "Польский",
    "pt": "Португальский",
    "ro": "Румынский",
    "ru": "Русский",
    "sv": "Шведский",
    "th": "Тайский",
    "tl": "Филиппинский",
    "tr": "Турецкий",
    "uk": "Украинский",
    "ur": "Урду",
    "vi": "Вьетнамский",
    "zh": "Китайский",
}

PROMPT_LANGUAGE_NAMES: dict[str, str] = {
    "ar": "Arabic",
    "bn": "Bengali",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "fa": "Persian",
    "fi": "Finnish",
    "fr": "French",
    "he": "Hebrew",
    "hi": "Hindi",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "ms": "Malay",
    "nl": "Dutch",
    "no": "Norwegian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sv": "Swedish",
    "th": "Thai",
    "tl": "Filipino",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "vi": "Vietnamese",
    "zh": "Chinese",
}

AUTO_LANGUAGE = "auto"
AUTO_LANGUAGE_LABEL = "Автоопределение моделью"


def language_label(code: str) -> str:
    if code == AUTO_LANGUAGE:
        return AUTO_LANGUAGE_LABEL
    return LANGUAGES.get(code, code.upper())


def language_display(code: str) -> str:
    if code == AUTO_LANGUAGE:
        return AUTO_LANGUAGE_LABEL
    return f"{language_label(code)} · {code}"


def prompt_language_name(code: str) -> str:
    return PROMPT_LANGUAGE_NAMES.get(code, code)


def supported_languages(model: dict[str, Any]) -> tuple[str, ...]:
    """Return the curated UI language set for a model, never infer it from marketing copy."""
    codes = model.get("ui_language_codes")
    if isinstance(codes, list):
        return tuple(code for code in codes if code in LANGUAGES)
    pairs = model.get("language_pairs", [])
    return tuple(sorted({code for pair in pairs for code in pair if code in LANGUAGES}))


def supports_language(model: dict[str, Any], code: str) -> bool:
    return code == AUTO_LANGUAGE or code in supported_languages(model)
