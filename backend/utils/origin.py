"""Reject browser writes before handlers run; CORS alone only hides responses."""
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse


def _origin(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https', 'tauri'} or not parsed.hostname:
            return None
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            return None
        port = parsed.port or {'http': 80, 'https': 443}.get(parsed.scheme)
        return parsed.scheme, parsed.hostname, port
    except ValueError:
        return None


class BrowserWriteOriginMiddleware:
    def __init__(self, app, allowed_origins):
        self.app = app
        self.allowed = {_origin(value) for value in allowed_origins} - {None}

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] in {'GET', 'HEAD', 'OPTIONS'}:
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        origins = headers.getlist('origin')
        if origins:
            origin = _origin(origins[0]) if len(origins) == 1 else None
            target = _origin(scope['scheme'] + '://' + headers.get('host', ''))
            allowed = origin is not None and (origin in self.allowed or origin == target)
        else:
            # CLI/native clients omit Origin. Browser cross-site form/fetch writes
            # still fail closed when Fetch Metadata identifies their source.
            allowed = headers.get('sec-fetch-site') != 'cross-site'
        if not allowed:
            response = JSONResponse({'detail': 'This website is not allowed to change Voicebox or generate audio.'}, status_code=403)
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)
