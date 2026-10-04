from dotlingo.locale_hints import code_from_langid, interface_language, keyboard_language


def test_langid_maps_known_layouts() -> None:
    assert code_from_langid(0x0419) == "ru"
    assert code_from_langid(0x0409) == "en"
    assert code_from_langid(0x0422) == "uk"
    assert code_from_langid(0x0804) == "zh"
    assert code_from_langid(0x0404) == "zh-Hant"
    assert code_from_langid(0x0C04) == "zh-Hant"


def test_unknown_langid_stays_empty() -> None:
    assert code_from_langid(0) == ""
    assert code_from_langid(0x007F) == ""


def test_live_hints_are_catalog_codes_or_empty() -> None:
    from dotlingo.languages import LANGUAGES

    for code in (keyboard_language(), interface_language()):
        assert code == "" or code in LANGUAGES


def test_api_locale_hints_shape(tmp_path) -> None:
    from dotlingo.api import Api
    from dotlingo.languages import LANGUAGES

    data = Api(tmp_path).getLocaleHints()["data"]
    assert set(data) == {"keyboard", "interface"}
    for code in data.values():
        assert code == "" or code in LANGUAGES
