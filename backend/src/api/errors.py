"""RFC 7807 (`application/problem+json`) ошибки kb-support API.

Соответствует схеме `Error` контракта `04_openapi.yaml`. `ProblemException`
поднимается в коде/зависимостях; зарегистрированный хендлер рендерит её в
problem+json с нужным статусом.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

PROBLEM_CONTENT_TYPE = "application/problem+json"
_ERROR_BASE = "https://api.rehome.one/errors"


class ProblemException(Exception):
    """Ошибка API в формате RFC 7807."""

    def __init__(
        self,
        *,
        status: int,
        title: str,
        type_: str,
        detail: str | None = None,
        errors: list[dict[str, str]] | None = None,
    ) -> None:
        self.status = status
        self.title = title
        self.type = type_
        self.detail = detail
        self.errors = errors
        super().__init__(title)

    @classmethod
    def bad_request(cls, detail: str | None = None) -> ProblemException:
        return cls(
            status=400,
            title="Bad Request",
            type_=f"{_ERROR_BASE}/bad-request",
            detail=detail,
        )

    @classmethod
    def unauthorized(cls, detail: str | None = None) -> ProblemException:
        return cls(
            status=401,
            title="Unauthorized",
            type_=f"{_ERROR_BASE}/unauthorized",
            detail=detail,
        )

    @classmethod
    def forbidden(cls, detail: str | None = None) -> ProblemException:
        return cls(
            status=403,
            title="Forbidden",
            type_=f"{_ERROR_BASE}/forbidden",
            detail=detail,
        )

    @classmethod
    def not_found(cls, detail: str | None = None) -> ProblemException:
        return cls(
            status=404,
            title="Not Found",
            type_=f"{_ERROR_BASE}/not-found",
            detail=detail,
        )

    @classmethod
    def conflict(cls, detail: str | None = None) -> ProblemException:
        return cls(
            status=409,
            title="Conflict",
            type_=f"{_ERROR_BASE}/conflict",
            detail=detail,
        )

    @classmethod
    def unprocessable(cls, detail: str | None = None) -> ProblemException:
        return cls(
            status=422,
            title="Unprocessable Entity",
            type_=f"{_ERROR_BASE}/unprocessable-entity",
            detail=detail,
        )

    @classmethod
    def bad_gateway(cls, detail: str | None = None) -> ProblemException:
        """502 — сбой оркестрации соседнего сервиса (например, kb-platform недоступен)."""
        return cls(
            status=502,
            title="Bad Gateway",
            type_=f"{_ERROR_BASE}/bad-gateway",
            detail=detail,
        )


def render_problem(exc: ProblemException) -> JSONResponse:
    """Собрать problem+json ответ из `ProblemException`."""
    body: dict[str, Any] = {"type": exc.type, "title": exc.title, "status": exc.status}
    if exc.detail is not None:
        body["detail"] = exc.detail
    if exc.errors is not None:
        body["errors"] = exc.errors
    return JSONResponse(status_code=exc.status, content=body, media_type=PROBLEM_CONTENT_TYPE)


async def problem_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI exception handler для `ProblemException`.

    Сигнатура принимает базовый `Exception` (требование реестра Starlette);
    фактический тип гарантируется регистрацией хендлера именно на ProblemException.
    """
    assert isinstance(exc, ProblemException)
    return render_problem(exc)
