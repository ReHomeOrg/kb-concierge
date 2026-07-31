"""Боевой reader статуса онбординга через доверенный m2m internal-эндпоинт rehome.one (Phase 1).

Концьерж (Keycloak-RS256) и rehome.one (своя Django-авторизация, HS256) — РАЗДЕЛЬНЫЕ системы
личности; passthrough Keycloak-токена rehome.one отвергает (проверено smoke-тестом). Поэтому
**service-to-service**: концьерж зовёт internal-эндпоинт rehome.one сервис-ключом
(`X-Internal-Service-Key`), передавая `keycloak_sub` (=on_behalf_of) и `email`; rehome.one
резолвит своего юзера (по keycloak_sub, при первом обращении auto-link по email) и отдаёт статус.

Контракт (internal, под сервис-ключом):
  GET /api/v1/internal/onboarding/status/?keycloak_sub&role=owner|tenant&email
  → тот же {role, steps:[{key,done}], ...}; key ∈ profile|phone|kyc|property

Маппинг шагов → `done_flag`-ов автомата (`flow_data.json`, `_map`). Любой сбой/не-200/404-нет-
связки → `None` (деградация FR-6.6): гид в режим ПУТИ, не роняется и не врёт. Без ПДн в логах
(email/sub не логируем).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import httpx

from api.tools.base import ToolContext

logger = logging.getLogger(__name__)

# Internal m2m-эндпоинт rehome.one (единый для обеих ролей; роль — query-параметром).
# Завершающий слэш — реальный роут; follow_redirects как страховка от 307.
_STATUS_PATH = "/api/v1/internal/onboarding/status/"
_SERVICE_KEY_HEADER = "X-Internal-Service-Key"


class PlatformStatusReader:
    """Читает статус онбординга у rehome.one через доверенный m2m internal-эндпоинт."""

    def __init__(
        self,
        *,
        base_url: str,
        service_key: str,
        timeout: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._service_key = service_key
        self._timeout = timeout
        # transport — тест-seam (httpx.MockTransport); в проде None (реальная сеть).
        self._transport = transport

    async def read(self, role: str, context: ToolContext) -> Mapping[str, bool] | None:
        if role not in ("owner", "tenant") or not context.on_behalf_of:
            return None
        params: dict[str, str] = {"keycloak_sub": str(context.on_behalf_of), "role": role}
        if context.email:
            params["email"] = context.email  # первичная связка личности (auto-link)
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                follow_redirects=True,  # 307 от FastAPI trailing-slash → следуем (страховка)
            ) as http:
                resp = await http.get(
                    _STATUS_PATH,
                    params=params,
                    headers={_SERVICE_KEY_HEADER: self._service_key},
                )
            if resp.status_code != 200:
                # status_code — не ПДн; логируем для диагностики (401=сервис-ключ,
                # 404=нет связки, 5xx=платформа). Тело/идентификаторы не логируем.
                logger.warning(
                    "onboarding.platform_status.non_200",
                    extra={"role": role, "status_code": resp.status_code},
                )
                return None
            payload = resp.json()
        except Exception:  # noqa: BLE001 — любая ошибка → режим ПУТИ (FR-6.6)
            logger.warning("onboarding.platform_status.unavailable", extra={"role": role})
            return None
        return _map(role, payload)


def _map(role: str, payload: Any) -> Mapping[str, bool] | None:
    """Платформенный OnboardingStatusResponse → `done_flag`-и автомата flow_data.json.

    Полная цепочка (owner: egrn_verified/payout_saved; tenant: solvency_confirmed)
    приходит из extended-режима internal-эндпоинта платформы (ключи egrn/payout/income,
    контракт #16). Если платформа их не отдала (старый билд / базовый режим) —
    `done.get(..., False)` → шаг трактуется как незавершённый, гид доведёт до него.
    """
    if not isinstance(payload, dict):
        return None
    steps_raw = payload.get("steps")
    if not isinstance(steps_raw, list):
        return None
    done = {
        str(s.get("key")): bool(s.get("done"))
        for s in steps_raw
        if isinstance(s, dict) and s.get("key")
    }
    if role == "owner":
        return {
            "account": True,  # запрос аутентифицирован → аккаунт есть
            "kyc_passed": done.get("kyc", False),
            "object_added": done.get("property", False),
            "egrn_verified": done.get("egrn", False),
            "payout_saved": done.get("payout", False),
        }
    # tenant: profile-минимум для брони = профиль И телефон подтверждены
    return {
        "account": True,
        "profile_complete": done.get("profile", False) and done.get("phone", False),
        "kyc_passed": done.get("kyc", False),
        "solvency_confirmed": done.get("income", False),
    }
