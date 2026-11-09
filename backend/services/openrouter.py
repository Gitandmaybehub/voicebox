"""Fixed-origin OpenRouter transport and private, redacted configuration."""
import asyncio
import json
import os
import re
import math
from datetime import datetime, timezone
from collections import defaultdict

from .. import config
from pathlib import Path
import sys
import tempfile
import threading

import httpx

BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "heygen/heygen-voice-1-turbo:free"
SOURCE_URL = "https://openrouter.ai/rankings/speech?view=week"
RANKINGS_URL = "https://openrouter.ai/api/frontend/v1/rankings/modality-models?routeSegment=speech&view=week"
CATALOG_URL = "https://openrouter.ai/api/frontend/v1/models/find?output_modalities=speech"
REFRESH_INTERVAL = 3600
CATALOG_ERROR = "Cannot refresh OpenRouter models. Try again later."
CAPABILITY_ERROR = "Cannot verify OpenRouter model capabilities. Try again later."
UNKNOWN_ERROR = "Selected model is not in the current speech catalog."
_refresh_tasks = {}
_lock = threading.Lock()


class OpenRouterError(ValueError):
    """Only fixed, non-provider-controlled messages may cross this boundary."""

    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def settings_path():
    # Deliberately independent of the development data directory / checkout.
    root = Path.home() / ("Library/Application Support" if sys.platform == "darwin" else ".local/share")
    return root / "voicebox-private" / "openrouter.json"


def _read():
    try:
        path = settings_path()
        if not path.exists():
            return {"model": DEFAULT_MODEL}
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError()
        validate_model(data.get("model", DEFAULT_MODEL))
        if "api_key" in data and not isinstance(data["api_key"], str):
            raise ValueError()
        return data
    except Exception:
        raise OpenRouterError("Cannot read private OpenRouter settings. Check file permissions.") from None


def status():
    data = _read()
    return {"configured": bool(data.get("api_key")), "model": data.get("model", DEFAULT_MODEL)}


def validate_model(model):
    if (not isinstance(model, str) or len(model) > 200
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9._-]*(?::[A-Za-z0-9][A-Za-z0-9_-]*)?", model)
            or ".." in model):
        raise OpenRouterError("Enter a valid OpenRouter model ID.")
    return model


def resolve_model(model=None):
    return validate_model(model or status()["model"])


def update_settings(patch):
    # Validate manually: Pydantic's default errors can echo a secret input.
    if not isinstance(patch, dict) or set(patch) - {"api_key", "model", "clear_key"}:
        raise OpenRouterError("Invalid OpenRouter settings payload.")
    key = patch.get("api_key")
    if key is not None and (not isinstance(key, str) or not 1 <= len(key.strip()) <= 512 or any(ord(c) < 33 or ord(c) > 126 for c in key.strip())):
        raise OpenRouterError("Enter a valid OpenRouter API key.")
    if "clear_key" in patch and not isinstance(patch["clear_key"], bool):
        raise OpenRouterError("clear_key must be a boolean.")
    if patch.get("clear_key") and key is not None:
        raise OpenRouterError("Clear the key or replace it, not both.")
    if "model" in patch:
        validate_model(patch["model"] if isinstance(patch["model"], str) else None)
    with _lock:
        data = _read()
        if "model" in patch:
            data["model"] = patch["model"]
        if patch.get("clear_key"):
            data.pop("api_key", None)
        elif key is not None:
            data["api_key"] = key.strip()
        path = settings_path()
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary = tempfile.mkstemp(dir=path.parent)
            with os.fdopen(fd, "w") as f:
                os.fchmod(f.fileno(), 0o600)
                json.dump(data, f)
            os.replace(temporary, path)
        except Exception:
            raise OpenRouterError("Cannot save private OpenRouter settings. Check file permissions.") from None
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
    return status()


def _key():
    key = _read().get("api_key")
    if not key:
        raise OpenRouterError("Add an OpenRouter API key in Settings first.")
    return key


def _check_status(code):
    if 200 <= code < 300:
        return
    messages = {
        400: "OpenRouter rejected the audio or request. Check the reference recording and selected model.",
        401: "OpenRouter rejected the API key. Replace it in Settings.",
        402: "OpenRouter requires available credits. Check your OpenRouter account.",
        403: "OpenRouter access is restricted. Check key permissions and account limits.",
        404: "The selected OpenRouter model is unavailable. Select another model explicitly.",
        413: "The OpenRouter reference recording is too large.",
        429: "OpenRouter is rate limited. Wait before manually retrying.",
    }
    raise OpenRouterError(
        messages.get(code, "OpenRouter is unavailable. Check your connection or try again later."),
        status_code=code if 400 <= code <= 599 else 502,
    )


async def request(path, *, authenticated=False, payload=None):
    """No redirects, URL overrides, retries, provider bodies, or credential logs."""
    headers = {"Authorization": "Bearer " + _key()} if authenticated else {}
    try:
        async with asyncio.timeout(180):
            async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15), follow_redirects=False, trust_env=False) as client:
                async with client.stream("POST" if payload is not None else "GET", BASE_URL + path, headers=headers, json=payload) as response:
                    _check_status(response.status_code)
                    body = bytearray()
                    async for part in response.aiter_bytes():
                        body.extend(part)
                        if len(body) > 64 * 1024 * 1024:
                            raise OpenRouterError("OpenRouter returned too much audio. Use shorter text chunks.")
                    return bytes(body), response.headers.get("content-type", "").split(";")[0]
    except OpenRouterError:
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise OpenRouterError("OpenRouter timed out. No automatic retry was made; check usage before retrying.") from None
    except Exception:
        raise OpenRouterError("Cannot reach OpenRouter. Check your connection and try again.") from None


async def _json(path, authenticated=False):
    raw, _ = await request(path, authenticated=authenticated)
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except Exception:
        raise OpenRouterError("OpenRouter returned an invalid response. Try again later.") from None


async def check_key():
    data = await _json("/key", authenticated=True)
    if not isinstance(data.get("data"), dict):
        raise OpenRouterError("OpenRouter returned an invalid key status.")
    return {"status": "ok"}


async def model_capability(model):
    validate_model(model)
    data = await _public_json(BASE_URL + "/models/" + model + "/endpoints")
    try:
        info = data["data"]
        endpoints = info["endpoints"]
        if not isinstance(endpoints, list) or not all(isinstance(e, dict) for e in endpoints):
            raise ValueError()
        if "speech" not in info.get("architecture", {}).get("output_modalities", []):
            return False, False
        active = [e for e in endpoints if type(e.get("status")) is int and e["status"] == 0]
        return bool(active), any(e.get("supports_voice_cloning") is True for e in active)
    except Exception:
        raise OpenRouterError(CAPABILITY_ERROR) from None


async def require_cloning(model):
    available, cloning = await model_capability(model)
    if not available or not cloning:
        raise OpenRouterError("Selected OpenRouter model currently cannot clone voices. Select another model explicitly.")


async def _public_json(url):
    """Only fixed public catalog URLs and structurally validated endpoint paths."""
    if url not in (RANKINGS_URL, CATALOG_URL):
        prefix = BASE_URL + "/models/"
        if not url.startswith(prefix) or not url.endswith("/endpoints"):
            raise OpenRouterError(CATALOG_ERROR)
        validate_model(url[len(prefix):-len("/endpoints")])
    try:
        async with asyncio.timeout(25):
            async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5),
                                         follow_redirects=False, trust_env=False) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        raise ValueError()
                    body = bytearray()
                    async for part in response.aiter_bytes():
                        body.extend(part)
                        if len(body) > 8 * 1024 * 1024:
                            raise ValueError()
                    data = json.loads(body)
                    if not isinstance(data, dict):
                        raise ValueError()
                    return data
    except Exception:
        raise OpenRouterError(CATALOG_ERROR) from None


def _now():
    return datetime.now(timezone.utc)


def _cache_path():
    return config.get_data_dir() / "cache" / "openrouter-catalog.json"


def _metadata(catalog):
    entries = catalog["data"]["models"]
    if not isinstance(entries, list) or not entries:
        raise ValueError()
    by_permaslug, by_id = {}, {}
    for item in entries:
        # Endpoint variant identifiers are official, including :free. Never infer
        # an API ID by stripping a date from the permanent identifier.
        endpoint = item.get("endpoint") or {}
        ident = validate_model(endpoint.get("model_variant_slug", item.get("slug")))
        permanent = validate_model(endpoint.get("model_variant_permaslug", item.get("permaslug")))
        if ident.rpartition(":")[2] == "free" and not permanent.endswith(":free"):
            raise ValueError()
        row = {"id": ident, "name": item["name"], "description": item.get("description", ""),
               "available": False, "supports_voice_cloning": False,
               "rank": None, "weekly_requests": None}
        if not isinstance(row["name"], str) or not isinstance(row["description"], str):
            raise ValueError()
        # Only public numeric price fields. Never persist provider routing data.
        pricing = endpoint.get("pricing_json") or endpoint.get("pricing") or item.get("pricing")
        if isinstance(pricing, dict):
            row["pricing"] = {k: v for k, v in pricing.items()
                              if isinstance(k, str) and len(k) < 100
                              and isinstance(v, (str, int, float)) and not isinstance(v, bool)
                              and _is_price(v)}
        if permanent in by_permaslug and by_permaslug[permanent]["id"] != ident:
            raise ValueError()
        by_permaslug[permanent] = row
        by_id[ident] = row
    return by_permaslug, by_id


def _is_price(value):
    try:
        return len(str(value)) < 100 and math.isfinite(float(value)) and float(value) >= 0
    except (ValueError, TypeError, OverflowError):
        return False


def _ranked_rows(payload, mapping):
    rows = payload["data"]
    if not isinstance(rows, list) or not rows:
        raise ValueError()
    counts, dates = defaultdict(int), set()
    for row in rows:
        permanent = validate_model(row["variant_permaslug"])
        count = row["count"]
        if type(count) is not int or not 0 <= count <= 2**53:
            raise ValueError()
        date = row["date"]
        datetime.strptime(date, "%Y-%m-%d %H:%M:%S")
        dates.add(date)
        counts[permanent] += count
    ordered = sorted(counts, key=lambda key: (-counts[key], key))[:15]
    if len(ordered) != 15:
        raise ValueError()
    result = []
    for rank, permanent in enumerate(ordered, 1):
        result.append({**mapping[permanent], "rank": rank, "weekly_requests": counts[permanent]})
    if len({r["id"] for r in result}) != 15:
        raise ValueError()
    return result, max(dates)


async def _capabilities(row, semaphore):
    async with semaphore:
        try:
            available, cloning = await model_capability(row["id"])
            return {**row, "available": available, "supports_voice_cloning": cloning}
        except OpenRouterError:
            return {**row, "available": False, "supports_voice_cloning": False,
                    "error": CAPABILITY_ERROR}


def _validate_cache(data):
    if set(data) != {"version", "fetched_at", "data_date", "models", "catalog"} or data["version"] != 1:
        raise ValueError()
    stamp = datetime.fromisoformat(data["fetched_at"])
    if stamp.tzinfo is None or stamp > _now():
        raise ValueError()
    datetime.strptime(data["data_date"], "%Y-%m-%d %H:%M:%S")
    models, catalog = data["models"], data["catalog"]
    if not isinstance(models, list) or len(models) != 15 or not isinstance(catalog, dict) or not catalog:
        raise ValueError()
    allowed = {"id", "name", "description", "available", "supports_voice_cloning", "rank", "weekly_requests", "pricing", "error"}
    for row in [*models, *catalog.values()]:
        if not isinstance(row, dict) or set(row) - allowed:
            raise ValueError()
        validate_model(row["id"])
        if not all(isinstance(row[k], str) and len(row[k]) <= 100000 for k in ("name", "description")):
            raise ValueError()
        if not all(type(row[k]) is bool for k in ("available", "supports_voice_cloning")):
            raise ValueError()
        if row["supports_voice_cloning"] and not row["available"]:
            raise ValueError()
        if row.get("error") not in (None, CAPABILITY_ERROR):
            raise ValueError()
        if "pricing" in row and (not isinstance(row["pricing"], dict) or not all(
                isinstance(k, str) and len(k) < 100 and _is_price(v) for k, v in row["pricing"].items())):
            raise ValueError()
    if len({r["id"] for r in models}) != 15:
        raise ValueError()
    for rank, row in enumerate(models, 1):
        if type(row["rank"]) is not int or row["rank"] != rank or type(row["weekly_requests"]) is not int or row["weekly_requests"] < 0:
            raise ValueError()
        if row["id"] not in catalog:
            raise ValueError()
    if any(a["weekly_requests"] < b["weekly_requests"] for a, b in zip(models, models[1:])):
        raise ValueError()
    for key, row in catalog.items():
        if key != row["id"] or row["rank"] is not None or row["weekly_requests"] is not None:
            raise ValueError()
    return data


def _read_cache():
    try:
        path = _cache_path()
        if path.stat().st_size > 8 * 1024 * 1024:
            return None
        return _validate_cache(json.loads(path.read_text()))
    except Exception:
        return None


def _write_cache(data):
    _validate_cache(data)
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(dir=path.parent)
        with os.fdopen(fd, "w") as file:
            json.dump(data, file, allow_nan=False)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


async def _refresh():
    rankings, catalog = await asyncio.gather(_public_json(RANKINGS_URL), _public_json(CATALOG_URL))
    mapping, by_id = _metadata(catalog)
    rows, data_date = _ranked_rows(rankings, mapping)
    semaphore = asyncio.Semaphore(4)
    rows = await asyncio.gather(*(_capabilities(row, semaphore) for row in rows))
    # Cache metadata for off-list saved selections. They are checked on demand.
    snapshot = {"version": 1, "fetched_at": _now().isoformat().replace("+00:00", "Z"),
                "data_date": data_date, "models": rows, "catalog": by_id}
    _write_cache(snapshot)
    return snapshot


async def _refresh_safe():
    try:
        return await _refresh(), None
    except Exception:
        return _read_cache(), CATALOG_ERROR


async def list_models(refresh=False):
    snapshot = _read_cache()
    fresh = snapshot is not None and (_now() - datetime.fromisoformat(snapshot["fetched_at"])).total_seconds() < REFRESH_INTERVAL
    key = (asyncio.get_running_loop(), str(_cache_path()))
    task = _refresh_tasks.get(key)
    error = None
    if task is not None or refresh or not fresh:
        if task is None:
            task = asyncio.create_task(_refresh_safe())
            _refresh_tasks[key] = task
            task.add_done_callback(lambda done: _refresh_tasks.pop(key, None) if _refresh_tasks.get(key) is done else None)
        snapshot, error = await asyncio.shield(task)
    models = snapshot["models"] if snapshot else []
    selected = None
    try:
        saved = status()["model"]
        selected = next((row for row in models if row["id"] == saved), None)
        if selected is None:
            metadata = snapshot["catalog"].get(saved) if snapshot else None
            if metadata is not None:
                selected = await _capabilities(metadata, asyncio.Semaphore(1))
            else:
                selected = {"id": saved, "name": saved, "description": "", "available": False,
                            "supports_voice_cloning": False, "rank": None, "weekly_requests": None,
                            "error": UNKNOWN_ERROR}
    except OpenRouterError:
        error = "Cannot read private OpenRouter settings. Check file permissions."
    return {"models": models, "selected_model": selected, "recommended_model": DEFAULT_MODEL,
            "source_url": SOURCE_URL, "fetched_at": snapshot["fetched_at"] if snapshot else None,
            "data_date": snapshot["data_date"] if snapshot else None,
            "stale": snapshot is None or error is not None or (_now() - datetime.fromisoformat(snapshot["fetched_at"])).total_seconds() >= REFRESH_INTERVAL,
            "refresh_interval_seconds": REFRESH_INTERVAL, **({"error": error} if error else {})}
