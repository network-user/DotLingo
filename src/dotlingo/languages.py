"""Language labels and conservative model capability helpers for the desktop UI."""

from __future__ import annotations

from typing import Any

LANGUAGES: dict[str, str] = {
    "ar": "Арабский",
    "bn": "Бенгальский",
    "bo": "Тибетский",
    "cs": "Чешский",
    "da": "Датский",
    "de": "Немецкий",
    "el": "Греческий",
    "en": "Английский",
    "es": "Испанский",
    "fa": "Персидский",
    "fi": "Финский",
    "fr": "Французский",
    "gu": "Гуджарати",
    "he": "Иврит",
    "hi": "Хинди",
    "hu": "Венгерский",
    "id": "Индонезийский",
    "it": "Итальянский",
    "ja": "Японский",
    "kk": "Казахский",
    "km": "Кхмерский",
    "ko": "Корейский",
    "mn": "Монгольский",
    "mr": "Маратхи",
    "ms": "Малайский",
    "my": "Бирманский",
    "nl": "Нидерландский",
    "no": "Норвежский",
    "pl": "Польский",
    "pt": "Португальский",
    "ro": "Румынский",
    "ru": "Русский",
    "sv": "Шведский",
    "ta": "Тамильский",
    "te": "Телугу",
    "th": "Тайский",
    "tl": "Филиппинский",
    "tr": "Турецкий",
    "ug": "Уйгурский",
    "uk": "Украинский",
    "ur": "Урду",
    "vi": "Вьетнамский",
    "yue": "Кантонский",
    "zh": "Китайский",
    "zh-Hant": "Китайский традиционный",
}

PROMPT_LANGUAGE_NAMES: dict[str, str] = {
    "ar": "Arabic",
    "bn": "Bengali",
    "bo": "Tibetan",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "fa": "Persian",
    "fi": "Finnish",
    "fr": "French",
    "gu": "Gujarati",
    "he": "Hebrew",
    "hi": "Hindi",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "kk": "Kazakh",
    "km": "Khmer",
    "ko": "Korean",
    "mn": "Mongolian",
    "mr": "Marathi",
    "ms": "Malay",
    "my": "Burmese",
    "nl": "Dutch",
    "no": "Norwegian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sv": "Swedish",
    "ta": "Tamil",
    "te": "Telugu",
    "th": "Thai",
    "tl": "Filipino",
    "tr": "Turkish",
    "ug": "Uyghur",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "vi": "Vietnamese",
    "yue": "Cantonese",
    "zh": "Chinese",
    "zh-Hant": "Traditional Chinese",
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
