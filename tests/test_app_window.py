import json

import pytest

from dotlingo.app import (
    _bridge_call_allowed,
    _guarded_script_notify,
    _local_ui_navigation,
    _merge_browser_arguments,
    _MicrosoftRedirect,
)


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


def test_bridge_call_allows_public_names_and_drops_private_api() -> None:
    assert _bridge_call_allowed("openPath") is True
    assert _bridge_call_allowed("_pywebviewAlert") is True
    assert _bridge_call_allowed("_grant_file") is False
    assert _bridge_call_allowed("_grant_dir") is False
    assert _bridge_call_allowed("_grant_write") is False
    assert _bridge_call_allowed("") is False
    assert _bridge_call_allowed(None) is False


def test_script_notify_drops_a_private_name_before_the_library() -> None:
    called: list[object] = []

    class Args:
        def get_WebMessageAsJson(self) -> str:
            return json.dumps(["_grant_file", json.dumps(["C:/secret.txt"]), "probe"])

    class Host:
        def _dotlingo_script_notify(self, _sender: object, args: object) -> None:
            called.append(args)

    _guarded_script_notify(Host(), None, Args())
    assert called == []


def test_script_notify_forwards_a_public_name() -> None:
    called: list[str] = []

    class Args:
        def get_WebMessageAsJson(self) -> str:
            return json.dumps(["openPath", json.dumps(["C:/out.txt"]), "probe"])

    class Host:
        def _dotlingo_script_notify(self, _sender: object, args: object) -> None:
            called.append(json.loads(args.get_WebMessageAsJson())[0])

    _guarded_script_notify(Host(), None, Args())
    assert called == ["openPath"]


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
