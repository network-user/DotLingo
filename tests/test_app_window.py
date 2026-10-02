from dotlingo.app import _merge_browser_arguments


def test_browser_arguments_keep_a_single_disable_features_flag() -> None:
    merged = _merge_browser_arguments("--disable-features=ElasticOverscroll")
    assert merged == "--disable-features=CalculateNativeWinOcclusion,ElasticOverscroll"


def test_browser_arguments_do_not_duplicate_the_occlusion_flag() -> None:
    current = "--disable-features=CalculateNativeWinOcclusion,ElasticOverscroll"
    assert _merge_browser_arguments(current) == current


def test_browser_arguments_add_the_flag_when_features_are_absent() -> None:
    assert _merge_browser_arguments("--disable-gpu") == (
        "--disable-gpu --disable-features=CalculateNativeWinOcclusion"
    )
