"""FastAPI application entry point для kb-concierge.

M0 (каркас): обработчик ошибок (RFC 7807) + observability (JSON-логи, request_id
middleware, Prometheus-метрики, liveness/readiness). Доменные роутеры (sessions,
policies, inbound/operator-reply) подключаются по мере эпиков M1+ (§10 ТЗ).
"""

from __future__ import annotations

from typing import Literal

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse, Response

from api import __version__
from api.config import get_settings
from api.db import get_session
from api.errors import ProblemException, problem_exception_handler
from api.observability.health import check_database, check_redis
from api.observability.logging import configure_logging, get_logger
from api.observability.metrics import MetricsMiddleware, metrics_response
from api.observability.request_id import RequestIdMiddleware

# Префикс доменного API (§10): /api/v1/concierge.
_API_PREFIX = "/api/v1/concierge"

configure_logging(get_settings().log_level)
_logger = get_logger("api")

app = FastAPI(
    title="kb-concierge",
    description="ИИ-агент-оркестратор экосистемы reHome «Консьерж» (по ТЗ v1.1)",
    version=__version__,
)

app.add_exception_handler(ProblemException, problem_exception_handler)
# RequestIdMiddleware добавляется последним → исполняется первым (request_id/
# correlation_id доступны всем внутренним слоям и логам запроса).
app.add_middleware(MetricsMiddleware)
app.add_middleware(RequestIdMiddleware)

# Доменные роутеры (M1+) под общим префиксом /api/v1/concierge — добавляются по эпикам.


class HealthzResponse(BaseModel):
    """Liveness probe response — фиксированная схема для контракта."""

    status: Literal["ok"]


@app.get(
    "/healthz",
    response_model=HealthzResponse,
    summary="Liveness probe",
    tags=["Infrastructure"],
)
def healthz() -> HealthzResponse:
    """200 OK, если процесс жив (не проверяет зависимости). K8s liveness probe."""
    return HealthzResponse(status="ok")


@app.get("/readyz", summary="Readiness probe", tags=["Infrastructure"])
async def readyz(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    """Готовность к трафику: БД (`SELECT 1`) — обязательна, недоступна → 503.

    Redis (кеш HTTP-клиентов, брокер воркеров) — мягкий статус: его недоступность
    деградирует кеш, но НЕ снимает готовность; отражается полем `redis` в теле."""
    try:
        await check_database(session)
    except Exception:
        _logger.warning("readiness check failed: database unreachable")
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "detail": "database unreachable"},
        )
    redis_ok = await check_redis(get_settings().redis_url)
    if not redis_ok:
        _logger.warning("readiness: redis unreachable (cache degraded, service still ready)")
    return JSONResponse(
        status_code=200,
        content={"status": "ready", "redis": "ok" if redis_ok else "degraded"},
    )


@app.get("/metrics", summary="Prometheus metrics", tags=["Infrastructure"])
def metrics() -> Response:
    """Метрики в формате Prometheus exposition."""
    return metrics_response()
