from __future__ import annotations

from pathlib import Path

from dotlingo.api import Api
from dotlingo.languages import supports_language
from dotlingo.models import catalog
from dotlingo.scratch import prepare_turn


def _model() -> dict:
    for model in catalog():
        if model.get("status") == "available" and supports_language(model, "ru"):
            return model
    raise AssertionError("В каталоге нет доступной модели с поддержкой ru.")


def test_translation_prompt_contains_source_and_target() -> None:
    model = _model()
    _system, user = prepare_turn(model, "Hello there", "en", "ru", [], "", "translate")
    assert "Hello there" in user
    assert "Russian" in user or "рус" in user.casefold()


def test_hy_mt2_ask_puts_the_reply_instruction_in_the_user_turn() -> None:
    model = _model()
    if model.get("prompt_style") != "hy-mt2":
        return
    system, user = prepare_turn(model, "Привет", "ru", "en", [], "", "ask")
    assert system == ""
    assert "Привет" in user
    assert "Assistant:" in user


def test_ask_scratch_validates_without_loading_weights(tmp_path: Path) -> None:
    api = Api(tmp_path)
    model = _model()
    empty = api.askScratch({"modelId": model["id"], "targetLang": "ru", "text": "  "})
    assert empty["ok"] is False
    assert empty["code"] == "empty"

    too_long = api.askScratch(
        {"modelId": model["id"], "sourceLang": "en", "targetLang": "ru", "text": "a" * 2001}
    )
    assert too_long["ok"] is False
    assert too_long["code"] == "too_long"

    same = api.askScratch(
        {"modelId": model["id"], "sourceLang": "ru", "targetLang": "ru", "text": "Текст"}
    )
    assert same["ok"] is False
    assert same["code"] == "same_language"

    missing = api.askScratch(
        {
            "modelId": model["id"],
            "sourceLang": "en",
            "targetLang": "ru",
            "text": "Hello",
            "mode": "translate",
        }
    )
    assert missing["ok"] is False
    assert missing["code"] == "model_missing"

    asked = api.askScratch(
        {
            "modelId": model["id"],
            "sourceLang": "en",
            "targetLang": "ru",
            "text": "Hello",
            "mode": "ask",
        }
    )
    assert asked["ok"] is False
    assert asked["code"] == "model_missing"
