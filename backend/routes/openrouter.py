"""OpenRouter settings, free key verification and public model discovery."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from ..services import openrouter as service

router = APIRouter()


async def handle_openrouter_error(request: Request, exc: service.OpenRouterError):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@router.get("/settings/openrouter")
async def get_settings():
    try:
        return service.status()
    except service.OpenRouterError as exc:
        raise HTTPException(503, str(exc)) from None


@router.put("/settings/openrouter")
async def put_settings(request: Request):
    # Do not let FastAPI validation echo any part of the credential payload.
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 4096:
            raise HTTPException(413, "OpenRouter settings payload is too large.")
    try:
        import json
        patch = json.loads(body)
    except Exception:
        raise HTTPException(400, "Invalid OpenRouter settings JSON.") from None
    try:
        return service.update_settings(patch)
    except service.OpenRouterError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None


@router.post("/settings/openrouter/test")
async def test_settings():
    try:
        return await service.check_key()
    except service.OpenRouterError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None


@router.get("/openrouter/models")
async def get_models():
    return await service.list_models()
