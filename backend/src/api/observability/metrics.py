"""Prometheus-метрики HTTP-запросов + endpoint `/metrics`.

`http_requests_total{method,endpoint,status}` (Counter) и
`http_request_duration_seconds{method,endpoint}` (Histogram). `endpoint` —
шаблон маршрута (не сырой путь) для антикардинальности.
"""

from __future__ import annotations

import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUESTS = Counter(
    "http_requests_total",
    "Всего обработанных HTTP-запросов",
    ["method", "endpoint", "status"],
)
DURATION = Histogram(
    "http_request_duration_seconds",
    "Длительность обработки HTTP-запроса (сек)",
    ["method", "endpoint"],
)


def _endpoint_label(scope: Scope) -> str:
    """Шаблон маршрута (`/tickets/{id}`), а не конкретный путь — антикардинальность."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str):
        return path
    raw = scope.get("path")
    return raw if isinstance(raw, str) else "unknown"


class MetricsMiddleware:
    """Считает количество и длительность HTTP-запросов."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self._app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            method = str(scope.get("method", "UNKNOWN"))
            endpoint = _endpoint_label(scope)
            REQUESTS.labels(method=method, endpoint=endpoint, status=str(status_code)).inc()
            DURATION.labels(method=method, endpoint=endpoint).observe(elapsed)


def metrics_response() -> Response:
    """Тело ответа `/metrics` в формате Prometheus."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
