"""Request-ID middleware (pure ASGI).

Pure ASGI rather than Starlette's BaseHTTPMiddleware because the latter wraps
responses in ways that interact badly with streaming (our SSE endpoint).

Honors an incoming X-Request-ID (so an upstream proxy's id survives),
generates one otherwise, binds it into structlog's contextvars — every log
line in the request carries it — and echoes it on the response.
"""

import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER = b"x-request-id"


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        incoming = dict(scope.get("headers") or []).get(HEADER)
        request_id = incoming.decode("latin-1") if incoming else str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=request_id)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((HEADER, request_id.encode("latin-1")))
            await send(message)

        try:
            await self._app(scope, receive, send_with_header)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
