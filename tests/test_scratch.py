from __future__ import annotations

from pathlib import Path

from dotlingo.api import Api, _scratch_pairs
from dotlingo.languages import supports_language
from dotlingo.models import catalog
from dotlingo.scratch import prepare_turn, scratch_context


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


def test_scratch_context_drops_another_language_and_a_short_turn() -> None:
    model = _model()
    japanese = [("Привет привет привет всем", "こんにちは、みなさん")]
    short = prepare_turn(model, "как дела", "ru", "ja", japanese, "тон", "translate")[1]
    assert "こんにちは" not in short
    assert "тон" in short
    long_source = "Voskresensky sings this song on Easter Sunday morning."
    mixed = prepare_turn(model, long_source, "ru", "en", japanese, "", "translate")[1]
    assert "こんにちは" not in mixed
    assert long_source in mixed
    same = (
        "The road was long and quiet before dawn.",
        "Дорога была длинной и тихой ещё до рассвета.",
    )
    kept = prepare_turn(
        model,
        "The harbour is quiet tonight and the lights are low.",
        "en",
        "ru",
        [same],
        "",
        "translate",
    )[1]
    assert "Дорога была длинной" in kept
    assert scratch_context([("Привет", "こんにちは")], "ja", "как дела") == []


def test_scratch_pairs_keep_only_the_active_direction() -> None:
    pairs = _scratch_pairs(
        [
            {
                "source": "Привет привет привет всем",
                "translation": "こんにちは",
                "sourceLang": "ru",
                "targetLang": "ja",
                "modelId": "hy",
            },
            {
                "source": "The road was long and quiet before dawn.",
                "translation": "Дорога была длинной и тихой ещё до рассвета.",
                "sourceLang": "en",
                "targetLang": "ru",
                "modelId": "hy",
            },
        ],
        source_lang="en",
        target_lang="ru",
        model_id="hy",
    )
    assert pairs == [(
        "The road was long and quiet before dawn.",
        "Дорога была длинной и тихой ещё до рассвета.",
    )]
    other_model = _scratch_pairs(
        [
            {
                "source": "The road was long and quiet before dawn.",
                "translation": "Дорога была длинной и тихой ещё до рассвета.",
                "sourceLang": "en",
                "targetLang": "ru",
                "modelId": "other",
            }
        ],
        source_lang="en",
        target_lang="ru",
        model_id="hy",
    )
    assert other_model == []


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
