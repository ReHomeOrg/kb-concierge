"""ASGI-middleware проброса request_id.

`X-Request-Id` из заголовка (или генерируется uuid4) → в contextvar (для логов)
и эхо-заголовком в ответ. Pure-ASGI (а не BaseHTTPMiddleware) — надёжный проброс
contextvar в обработчик (тот же таск).
"""

from __future__ import annotations

import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.observability.context import request_id_var

_HEADER = b"x-request-id"


class RequestIdMiddleware:
    """Проставляет request_id в контекст запроса и в заголовок ответа."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        incoming = headers.get(_HEADER, b"").decode("latin-1").strip()
        request_id = incoming or str(uuid.uuid4())
        token = request_id_var.set(request_id)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers") or [])
                response_headers.append((_HEADER, request_id.encode("latin-1")))
                message = {**message, "headers": response_headers}
            await send(message)

        try:
            await self._app(scope, receive, send_with_header)
        finally:
            request_id_var.reset(token)
