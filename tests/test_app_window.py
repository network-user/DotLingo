import pytest

from dotlingo.app import _local_ui_navigation, _merge_browser_arguments, _MicrosoftRedirect


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


def test_navigation_allows_only_the_local_ui() -> None:
    assert _local_ui_navigation("about:blank", port=1234) is True
    assert _local_ui_navigation("http://127.0.0.1:1234/index.html", port=1234) is True
    assert _local_ui_navigation("http://localhost:1234/", port=1234) is True
    assert _local_ui_navigation("https://evil.example/", port=1234) is False
    assert _local_ui_navigation("file:///C:/Windows/win.ini", port=1234) is False
    assert _local_ui_navigation("data:text/html,hi", port=1234) is False
    assert _local_ui_navigation("javascript:alert(1)", port=1234) is False
    assert _local_ui_navigation("http://127.0.0.1:9/index.html", port=1234) is False
    assert _local_ui_navigation("http://user:pass@127.0.0.1:1234/", port=1234) is False
    assert _local_ui_navigation("http://127.0.0.1:1234/", port=None) is False


def test_webview_redirect_rejects_a_foreign_host() -> None:
    with pytest.raises(ValueError, match="доменов Microsoft"):
        _MicrosoftRedirect().redirect_request(
            None, None, 302, "Found", {}, "https://evil.example/setup.exe"
        )
