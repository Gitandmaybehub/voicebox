"""Fixed-origin OpenRouter transport and private, redacted configuration."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

import httpx

BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "heygen/heygen-voice-1"
MODEL_NAMES = {
    DEFAULT_MODEL: "HeyGen Voice",
    "heygen/heygen-voice-1-turbo": "HeyGen Voice Turbo",
    "fish-audio/drama-3": "Fish Audio Drama 3",
}
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
        return json.loads(path.read_text())
    except Exception:
        raise OpenRouterError("Cannot read private OpenRouter settings. Check file permissions.") from None


def status():
    data = _read()
    return {"configured": bool(data.get("api_key")), "model": data.get("model", DEFAULT_MODEL)}


def validate_model(model):
    if model not in MODEL_NAMES:
        raise OpenRouterError("Select HeyGen Voice, HeyGen Voice Turbo, or Fish Audio Drama 3.")
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
    data = await _json("/models/" + model + "/endpoints")
    try:
        endpoints = data["data"]["endpoints"]
        if not isinstance(endpoints, list):
            raise ValueError()
        active = [e for e in endpoints if e.get("status") == 0]
        return bool(active), any(e.get("supports_voice_cloning") is True for e in active)
    except Exception:
        raise OpenRouterError("Cannot verify OpenRouter model capabilities. Try again later.") from None


async def require_cloning(model):
    available, cloning = await model_capability(model)
    if not available or not cloning:
        raise OpenRouterError("Selected OpenRouter model currently cannot clone voices. Select another model explicitly.")


async def list_models():
    async def row(model, name):
        try:
            available, cloning = await model_capability(model)
            error = None
        except OpenRouterError as exc:
            available, cloning, error = False, False, str(exc)
        return {"id": model, "name": name, "description": "Cloud voice cloning", "available": available, "supports_voice_cloning": cloning, "error": error}
    return {"models": await asyncio.gather(*(row(m, n) for m, n in MODEL_NAMES.items())), "recommended_model": DEFAULT_MODEL}
