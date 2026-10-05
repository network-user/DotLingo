"""Curated Hugging Face GGUF market for local translation.

The repository list is fixed. Refresh reads public metadata only when the
user asks. A file can be downloaded after its commit, size and SHA-256 are
taken from the Hugging Face tree API and checked again at download time.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotlingo.models import _valid_market_record, upsert_market_model

_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}/[A-Za-z0-9][A-Za-z0-9._-]{0,80}")
_QUANT_TOKENS = (
    "IQ4_XS",
    "Q4_K_M",
    "Q4_K_S",
    "Q4_0",
    "Q5_K_M",
    "Q5_K_S",
    "Q5_0",
    "Q6_K",
    "Q8_0",
)
_ROLE_QUANTS = {
    "speed": ("Q4_K_S", "IQ4_XS", "Q4_0"),
    "balance": ("Q4_K_M", "Q5_K_S"),
    "quality": ("Q6_K", "Q5_K_M", "Q8_0"),
}
_ROLE_LABELS = {
    "speed": "Быстрее на CPU",
    "balance": "Баланс Q4",
    "quality": "Точнее квант",
}
_ROLE_NOTES = {
    "speed": "Этот квант меньше и на CPU обычно быстрее.",
    "balance": "Q4_K_M обычно ближе к качеству более тяжёлых квантов и быстрее их на CPU.",
    "quality": "Квант точнее Q4, файл больше, на CPU генерация медленнее.",
}

_HY_NOTE = (
    "Автор заявляет взаимный перевод десятков языков. "
    "В выборе проекта для этой загрузки включены только en и ru: остальные коды с карточкой не сверялись."
)
_QWEN_LANGUAGE_CODES = (
    "ar", "bn", "cs", "da", "de", "el", "en", "es", "fa", "fi", "fr", "he", "hi", "hu", "id", "it",
    "ja", "ko", "ms", "nl", "no", "pl", "pt", "ro", "ru", "sv", "th", "tl", "tr", "uk", "ur", "vi", "zh",
)
_QWEN_NOTE = (
    "Подборка из заявленной многоязычной поддержки Qwen3. "
    "В каталоге по умолчанию Qwen нет. Качество отдельных пар не измерялось."
)

SOURCES: tuple[dict[str, Any], ...] = (
    {
        "repo": "tencent/HY-MT1.5-1.8B-GGUF",
        "family": "HY-MT1.5",
        "parameters": "1.8B",
        "prompt_profile": "hy",
        "languages": ["en", "ru"],
        "language_note": _HY_NOTE,
        "summary": (
            "Специализированная модель перевода, поколение 1.5, размер 1.8B. "
            "В карточке есть пример llama.cpp. На CPU это самый лёгкий вариант рынка. "
            "Качество пары в DotLingo не измерялось."
        ),
        "caveat": "Память и скорость на Windows не измерялись.",
    },
    {
        "repo": "tencent/HY-MT1.5-7B-GGUF",
        "family": "HY-MT1.5",
        "parameters": "7B",
        "prompt_profile": "hy",
        "languages": ["en", "ru"],
        "language_note": _HY_NOTE,
        "summary": (
            "Та же переводческая серия 1.5, но 7B. На CPU заметно тяжелее 1.8B. "
            "Качество пары в DotLingo не измерялось."
        ),
        "caveat": "Память и скорость на Windows не измерялись.",
    },
    {
        "repo": "tencent/Hy-MT2-1.8B-GGUF",
        "family": "Hy-MT2",
        "parameters": "1.8B",
        "prompt_profile": "hy",
        "languages": ["en", "ru"],
        "language_note": _HY_NOTE,
        "summary": (
            "Более новая переводческая модель Tencent, 1.8B. "
            "Часть GGUF этого семейства требует ядро llama.cpp с поддержкой Hunyuan. "
            "Качество пары в DotLingo не измерялось."
        ),
        "caveat": "Если установленный runtime не откроет файл, перевод не стартует. Windows не проверялся.",
    },
    {
        "repo": "tencent/Hy-MT2-7B-GGUF",
        "family": "Hy-MT2",
        "parameters": "7B",
        "prompt_profile": "hy",
        "languages": ["en", "ru"],
        "language_note": _HY_NOTE,
        "summary": (
            "Hy-MT2 7B, переводческая модель крупнее 1.8B. "
            "На CPU она медленнее лёгких квантов. Качество пары в DotLingo не измерялось."
        ),
        "caveat": "Возможна зависимость от ядра Hunyuan в llama.cpp. Windows не проверялся.",
    },
    {
        "repo": "Qwen/Qwen3-4B-GGUF",
        "family": "Qwen3",
        "parameters": "4B",
        "prompt_profile": "qwen",
        "languages": None,
        "language_note": _QWEN_NOTE,
        "summary": (
            "Общая многоязычная модель, не специализированная для перевода. "
            "Q4 уже есть в каталоге; здесь же более точные кванты того же репозитория."
        ),
        "caveat": "Режим /no_think запрашивается, как у Qwen3 в каталоге. Скорость на Windows не измерялась.",
    },
    {
        "repo": "Qwen/Qwen3-8B-GGUF",
        "family": "Qwen3",
        "parameters": "8B",
        "prompt_profile": "qwen",
        "languages": None,
        "language_note": _QWEN_NOTE,
        "summary": (
            "Общая многоязычная модель крупнее Qwen3 4B. На CPU Q4 ещё практичен, Q6 и Q8 медленнее. "
            "Качество отдельных пар не измерялось."
        ),
        "caveat": "Это не переводческая модель. Скорость на Windows не измерялась.",
    },
)


class MarketError(RuntimeError):
    pass


class _HuggingFaceRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        if not _trusted_https(newurl):
            raise MarketError("Перенаправление ушло с Hugging Face.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def empty_cache() -> dict[str, Any]:
    return {"fetched_at": None, "offers": [], "errors": [], "stale": False}


def load_market_cache(root: Path) -> dict[str, Any]:
    path = Path(root) / "market_cache.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_cache()
    if not isinstance(raw, dict):
        return empty_cache()
    offers = [item for item in raw.get("offers") or [] if _valid_market_record(item)]
    errors = [
        item
        for item in raw.get("errors") or []
        if isinstance(item, dict) and isinstance(item.get("repo"), str) and isinstance(item.get("error"), str)
    ]
    return {
        "fetched_at": raw.get("fetched_at") if isinstance(raw.get("fetched_at"), str) else None,
        "offers": offers,
        "errors": errors,
        "stale": bool(raw.get("stale")),
    }


def find_cached_offer(root: Path, offer_id: str) -> dict[str, Any] | None:
    for offer in load_market_cache(root)["offers"]:
        if offer.get("id") == offer_id:
            return offer
    return None


def match_market_link(url: str, offers: list[dict[str, Any]]) -> dict[str, Any]:
    """Match a Hugging Face link to a cached curated file. Does not download."""
    repo, filename = _parse_market_link(url)
    same = [
        item
        for item in offers
        if isinstance(item, dict) and item.get("repo") == repo and item.get("id")
    ]
    if filename:
        found = next((item for item in same if item.get("filename") == filename), None)
        if found is None:
            raise MarketError("Этого файла нет в текущем списке. Обновите рынок и выберите его там.")
        return {
            "repo": repo,
            "filename": filename,
            "offer_id": found["id"],
            "offer_ids": [found["id"]],
        }
    if not same:
        raise MarketError("В текущем списке нет файлов этого репозитория. Обновите рынок.")
    return {
        "repo": repo,
        "filename": "",
        "offer_id": None,
        "offer_ids": [str(item["id"]) for item in same],
    }


def refresh_market(root: Path) -> dict[str, Any]:
    """Read the curated repositories. Does not download weights."""
    base = Path(root)
    base.mkdir(parents=True, exist_ok=True)
    offers: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for source in SOURCES:
        try:
            offers.extend(load_source(source))
        except MarketError as exc:
            errors.append({"repo": str(source["repo"]), "error": str(exc)})
    previous = load_market_cache(base)
    stale = False
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not offers and errors and previous["offers"]:
        offers = previous["offers"]
        fetched_at = previous["fetched_at"] or fetched_at
        stale = True
    payload = {"fetched_at": fetched_at, "offers": offers, "errors": errors, "stale": stale}
    _write_json(base / "market_cache.json", payload)
    return payload


def confirm_offer(offer: dict[str, Any]) -> dict[str, Any]:
    """Re-read one file from Hugging Face and refuse the download if the pin moved."""
    source = _source_for(str(offer.get("repo") or ""))
    if source is None:
        raise MarketError("Репозиторий не входит в рынок DotLingo.")
    filename = str(offer.get("filename") or "")
    meta = _model_meta(source["repo"])
    revision = str(meta.get("sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise MarketError("Hugging Face не вернул ревизию репозитория.")
    if revision != offer.get("revision"):
        raise MarketError("Ревизия на Hugging Face изменилась. Обновите рынок.")
    match = _file_from_tree(source, revision, _license_name(meta), _tree(source["repo"], revision), filename)
    if match is None:
        raise MarketError("Файл больше не найден в этой ревизии. Обновите рынок.")
    same_hash = match["sha256"].lower() == str(offer.get("sha256") or "").lower()
    if not same_hash or match["size_bytes"] != offer.get("size_bytes"):
        raise MarketError("Контрольная сумма файла изменилась. Обновите рынок.")
    return match


def register_offer(offer: dict[str, Any], root: Path) -> dict[str, Any]:
    return upsert_market_model(offer, root)


def load_source(source: dict[str, Any]) -> list[dict[str, Any]]:
    meta = _model_meta(source["repo"])
    if _is_gated(meta.get("gated")):
        raise MarketError("Репозиторий закрыт. DotLingo не скачивает gated-модели.")
    revision = str(meta.get("sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise MarketError("Hugging Face не вернул ревизию репозитория.")
    tree = _tree(source["repo"], revision)
    return select_offers(source, revision, _license_name(meta), tree)


def select_offers(
    source: dict[str, Any],
    revision: str,
    license_name: str,
    tree: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_quant: dict[str, dict[str, Any]] = {}
    for item in tree:
        parsed = _parse_file(item)
        if parsed is None:
            continue
        by_quant.setdefault(parsed["quant"], parsed)
    chosen: list[dict[str, Any]] = []
    used: set[str] = set()
    for role, quants in _ROLE_QUANTS.items():
        for quant in quants:
            parsed = by_quant.get(quant)
            if parsed is None or parsed["filename"] in used:
                continue
            record = _record(source, revision, license_name, parsed, role)
            if record is not None:
                chosen.append(record)
                used.add(parsed["filename"])
            break
    return chosen


def _parse_file(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    filename = item.get("path") or item.get("rfilename")
    if not isinstance(filename, str) or not _plain_gguf(filename):
        return None
    lfs = item.get("lfs") if isinstance(item.get("lfs"), dict) else {}
    digest = str(lfs.get("oid") or "")
    if digest.startswith("sha256:"):
        digest = digest.split(":", 1)[1]
    digest = digest.lower()
    size = lfs.get("size")
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or not isinstance(size, int) or size < 1024 * 1024:
        return None
    quant = _quant_of(filename)
    if quant is None:
        return None
    return {"filename": filename, "sha256": digest, "size": size, "quant": quant}


def _record(
    source: dict[str, Any],
    revision: str,
    license_name: str,
    parsed: dict[str, Any],
    role: str,
) -> dict[str, Any] | None:
    filename = parsed["filename"]
    repo = str(source["repo"])
    digest = hashlib.sha256(f"{repo}\n{revision}\n{filename}".encode()).hexdigest()
    languages = source.get("languages")
    if not languages:
        languages = _qwen_codes()
    record = {
        "id": f"market-{digest[:12]}",
        "name": f"{source['family']} {source['parameters']} · {parsed['quant']}",
        "status": "available",
        "family": source["family"],
        "parameters": source["parameters"],
        "ui_language_codes": list(languages),
        "ui_language_note": source["language_note"],
        "language_pairs": [],
        "repo": repo,
        "revision": revision,
        "filename": filename,
        "format": "GGUF",
        "quantization": parsed["quant"],
        "size_bytes": parsed["size"],
        "sha256": parsed["sha256"],
        "license": license_name,
        "license_url": f"https://huggingface.co/{repo}",
        "card_url": f"https://huggingface.co/{repo}",
        "runtime": "llama-cpp-python",
        "runtime_adapter": "gguf-llama-cpp-market",
        "runtime_range": "Совместимость этой ревизии на Windows не проверена",
        "default_context": 4096,
        "max_output_tokens": 1800,
        "estimated_ram_gb": None,
        "estimated_vram_gb": None,
        "memory_estimate_note": "Потребление RAM не измерялось. На CPU Q4 обычно быстрее Q6 и Q8.",
        "tested_on_windows": False,
        "append_no_think": source["prompt_profile"] == "qwen",
        "custom": False,
        "role": role,
        "role_label": _ROLE_LABELS[role],
        "ui_description": source["summary"],
        "ui_details": f"{source['caveat']} {_ROLE_NOTES[role]}",
        "notes": source["summary"],
    }
    if source.get("prompt_profile") == "hy" and source.get("family") == "Hy-MT2":
        # Те же карточка и сэмплинг, что у закреплённого Hy-MT2 в каталоге.
        record["prompt_style"] = "hy-mt2"
        record["sampling"] = {
            "temperature": 0.7,
            "top_p": 0.6,
            "top_k": 20,
            "repeat_penalty": 1.05,
        }
    if not _valid_market_record(record):
        return None
    return record


def _file_from_tree(
    source: dict[str, Any],
    revision: str,
    license_name: str,
    tree: list[dict[str, Any]],
    filename: str,
) -> dict[str, Any] | None:
    for item in tree:
        parsed = _parse_file(item)
        if parsed is None or parsed["filename"] != filename:
            continue
        role = _role_for_quant(parsed["quant"])
        if role is None:
            return None
        return _record(source, revision, license_name, parsed, role)
    return None


def _role_for_quant(quant: str) -> str | None:
    for role, quants in _ROLE_QUANTS.items():
        if quant in quants:
            return role
    return None


def _model_meta(repo: str) -> dict[str, Any]:
    payload = _fetch_json(f"https://huggingface.co/api/models/{urllib.parse.quote(repo, safe='/')}")
    if not isinstance(payload, dict):
        raise MarketError("Hugging Face вернул не карточку модели.")
    return payload


def _tree(repo: str, revision: str) -> list[dict[str, Any]]:
    quoted = urllib.parse.quote(repo, safe="/")
    payload = _fetch_json(f"https://huggingface.co/api/models/{quoted}/tree/{revision}")
    if not isinstance(payload, list):
        raise MarketError("Hugging Face не вернул список файлов.")
    return [item for item in payload if isinstance(item, dict)]


def _fetch_json(url: str, timeout: float = 20) -> Any:
    if not _trusted_https(url):
        raise MarketError("Источник рынка отсутствует в списке доверенных HTTPS-доменов.")
    request = urllib.request.Request(url, headers={"User-Agent": "DotLingo/0.1", "Accept": "application/json"})
    opener = urllib.request.build_opener(_HuggingFaceRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            final = response.geturl()
            if not _trusted_https(final):
                raise MarketError("Ответ пришёл не с Hugging Face.")
            return json.loads(response.read().decode("utf-8"))
    except MarketError:
        raise
    except urllib.error.HTTPError as exc:
        raise MarketError(f"Hugging Face ответил {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise MarketError("Не удалось прочитать карточку на Hugging Face.") from exc


def _license_name(meta: dict[str, Any]) -> str:
    card = meta.get("cardData") if isinstance(meta.get("cardData"), dict) else {}
    license_name = card.get("license") or meta.get("license")
    if isinstance(license_name, list):
        license_name = ", ".join(str(item) for item in license_name if str(item).strip())
    if isinstance(license_name, str) and license_name.strip():
        return license_name.strip()[:80]
    return "Не указана"


def _is_gated(value: Any) -> bool:
    return value not in (None, False, "false", "")


def _quant_of(filename: str) -> str | None:
    upper = filename.upper()
    for token in _QUANT_TOKENS:
        if token in upper:
            return token
    return None


def _plain_gguf(filename: str) -> bool:
    if Path(filename).name != filename or any(char in filename for char in ("/", "\\", ":")):
        return False
    lowered = filename.lower()
    if not lowered.endswith(".gguf") or "mmproj" in lowered:
        return False
    return re.search(r"-\d+-of-\d+", lowered) is None


def _trusted_https(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (
        host == "huggingface.co" or host.endswith(".huggingface.co") or host.endswith(".hf.co")
    )


def _parse_market_link(url: str) -> tuple[str, str]:
    text = str(url or "").strip()
    parsed = urllib.parse.urlparse(text)
    host = (parsed.hostname or "").lower()
    trusted = parsed.scheme == "https" and (
        host in {"huggingface.co", "hf.co"}
        or host.endswith(".huggingface.co")
        or host.endswith(".hf.co")
    )
    if not trusted or parsed.username or parsed.password:
        raise MarketError("Нужна ссылка https на huggingface.co из закреплённого рынка.")
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 2 or not _REPO.fullmatch(f"{parts[0]}/{parts[1]}"):
        raise MarketError("Нужна ссылка https на huggingface.co из закреплённого рынка.")
    repo = f"{parts[0]}/{parts[1]}"
    if _source_for(repo) is None:
        raise MarketError("Этот репозиторий не входит в рынок DotLingo.")
    if len(parts) == 2 or (parts[2] == "tree" and len(parts) <= 4):
        return repo, ""
    if parts[2] in {"blob", "resolve"} and len(parts) == 5 and _plain_gguf(parts[4]):
        return repo, parts[4]
    raise MarketError("Ссылка должна вести на репозиторий рынка или на один файл .gguf.")


def _source_for(repo: str) -> dict[str, Any] | None:
    for source in SOURCES:
        if source["repo"] == repo:
            return source
    return None


def _qwen_codes() -> list[str]:
    return list(_QWEN_LANGUAGE_CODES)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f"{path.name}.", suffix=".tmp", delete=False
    ) as handle:
        temp_path = Path(handle.name)
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


