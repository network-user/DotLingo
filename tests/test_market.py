from __future__ import annotations

from pathlib import Path

import pytest

import dotlingo.api as api_module
import dotlingo.market as market
from dotlingo.market import MarketError, select_offers
from dotlingo.models import get_model
from tests.test_api import _make_api

REV = "b" * 40
SHA_Q4 = "a" * 64
SHA_Q6 = "c" * 64
SHA_Q8 = "d" * 64

SOURCE = {
    "repo": "tencent/HY-MT1.5-1.8B-GGUF",
    "family": "HY-MT1.5",
    "parameters": "1.8B",
    "prompt_profile": "hy",
    "languages": ["en", "ru"],
    "language_note": "только en и ru",
    "summary": "Короткое описание для теста.",
    "caveat": "Скорость не измерялась.",
}


def _file(name: str, size: int, sha: str) -> dict:
    return {"path": name, "size": size, "lfs": {"oid": sha, "size": size}}


def _tree() -> list[dict]:
    return [
        _file("HY-MT1.5-1.8B-Q4_K_M.gguf", 1133080512, SHA_Q4),
        _file("HY-MT1.5-1.8B-Q6_K.gguf", 1474785216, SHA_Q6),
        _file("HY-MT1.5-1.8B-Q8_0.gguf", 1908528288, SHA_Q8),
        _file("HY-MT1.5-1.8B-Q4_K_M-00001-of-00002.gguf", 1133080512, SHA_Q4),
        _file("sub/HY-MT1.5-1.8B-Q4_K_M.gguf", 1133080512, SHA_Q4),
        {"path": "README.md", "size": 100, "lfs": {"oid": SHA_Q4, "size": 100}},
        {"path": "no-hash-Q4_K_M.gguf", "size": 1133080512},
    ]


def test_select_offers_keeps_q4_and_q6_only() -> None:
    offers = select_offers(SOURCE, REV, "apache-2.0", _tree())
    assert [item["quantization"] for item in offers] == ["Q4_K_M", "Q6_K"]
    assert [item["role"] for item in offers] == ["balance", "quality"]
    assert offers[0]["append_no_think"] is False
    assert offers[0]["sha256"] == SHA_Q4
    assert offers[0]["id"].startswith("market-")
    assert offers[0]["ui_language_codes"] == ["en", "ru"]


def test_refresh_keeps_previous_offers_when_huggingface_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    offer = select_offers(SOURCE, REV, "apache-2.0", _tree())[0]

    def succeed(source: dict) -> list[dict]:
        if source["repo"] == SOURCE["repo"]:
            return [offer]
        return []

    monkeypatch.setattr(market, "load_source", succeed)
    first = market.refresh_market(tmp_path)
    assert len(first["offers"]) == 1
    assert first["stale"] is False

    def fail(source: dict) -> list[dict]:
        raise MarketError("нет сети")

    monkeypatch.setattr(market, "load_source", fail)
    second = market.refresh_market(tmp_path)
    assert second["stale"] is True
    assert second["offers"][0]["id"] == offer["id"]
    assert second["errors"]


def test_confirm_offer_rejects_a_moved_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    offer = select_offers(SOURCE, REV, "apache-2.0", _tree())[0]
    monkeypatch.setattr(
        market,
        "_model_meta",
        lambda repo: {"sha": "e" * 40, "cardData": {"license": "apache-2.0"}, "gated": False},
    )
    with pytest.raises(MarketError, match="Ревизия"):
        market.confirm_offer(offer)


def test_market_download_registers_the_confirmed_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    offer = select_offers(SOURCE, REV, "apache-2.0", _tree())[0]
    monkeypatch.setattr(market, "load_source", lambda source: [offer] if source["repo"] == SOURCE["repo"] else [])
    api = _make_api(tmp_path, sync=True)
    assert api.refreshMarket()["ok"] is True
    listed = api.listMarket()["data"]
    assert listed["offers"][0]["id"] == offer["id"]
    assert listed["offers"][0]["installed"] is False

    monkeypatch.setattr(api_module, "confirm_offer", lambda cached: offer)
    seen: list[dict] = []

    def fake_download(model: dict, root: Path, *, cancel: object, on_progress: object) -> Path:
        seen.append(model)
        return root / model["id"] / model["filename"]

    monkeypatch.setattr(api_module, "download_model", fake_download)
    assert api.downloadMarketModel(offer["id"])["ok"] is True
    assert seen[0]["sha256"] == SHA_Q4
    stored = get_model(offer["id"], api.models_dir)
    assert stored["repo"] == SOURCE["repo"]
    assert api.downloadMarketModel("market-not-in-cache")["ok"] is False
