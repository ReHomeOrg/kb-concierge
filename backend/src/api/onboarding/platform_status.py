"""Боевой reader статуса онбординга: делегированное self-scoped чтение платформы (Phase 1).

После `NullStatusReader` (режим ПУТИ) — гид узнаёт РЕАЛЬНУЮ позицию пользователя, читая
статус-эндпоинты платформы ОТ ИМЕНИ пользователя (G7). Read-only → делегирование через
passthrough пользовательского токена (`DelegatedUserTokenProvider`), CC-1 token-exchange
НЕ требуется.

Контракт платформы (#16):
  GET /api/v1/landlord/onboarding/status      (owner)
  GET /api/v1/verification/onboarding/status  (tenant)
  → {role, steps:[{key,done}], current_step, complete, ...}; key ∈ profile|phone|kyc|property

Маппинг платформенных шагов → `done_flag`-ов автомата (`flow_data.json`). Любой сбой/
недоступность/не-200 → `None` (деградация FR-6.6): гид падает в режим ПУТИ, не роняется и
не врёт о прогрессе. Без ПДн (читаем только булевы флаги завершённости).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import httpx

from api.clients.auth import TokenProvider
from api.tools.base import ToolContext

logger = logging.getLogger(__name__)

# Статус-эндпоинт платформы по роли онбординга.
_PATH_BY_ROLE = {
    "owner": "/api/v1/landlord/onboarding/status",
    "tenant": "/api/v1/verification/onboarding/status",
}


class PlatformStatusReader:
    """Читает статус онбординга у платформы делегированно (self-scoped) и маппит в флаги."""

    def __init__(
        self,
        *,
        base_url: str,
        token_provider: TokenProvider,
        timeout: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._token = token_provider
        self._timeout = timeout
        # transport — тест-seam (httpx.MockTransport); в проде None (реальная сеть).
        self._transport = transport

    async def read(self, role: str, context: ToolContext) -> Mapping[str, bool] | None:
        path = _PATH_BY_ROLE.get(role)
        if path is None or not context.on_behalf_of:
            return None
        try:
            # Делегирование в самом токене (passthrough токена пользователя) — платформа
            # применяет права ПОЛЬЗОВАТЕЛЯ (self-scoped), CC-1 не нужен. Сбой → деградация.
            token = await self._token.get_token(on_behalf_of=context.on_behalf_of)
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=self._timeout, transport=self._transport
            ) as http:
                resp = await http.get(path, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code != 200:
                return None
            payload = resp.json()
        except Exception:  # noqa: BLE001 — любая ошибка чтения → режим ПУТИ (FR-6.6)
            logger.warning("onboarding.platform_status.unavailable", extra={"role": role})
            return None
        return _map(role, payload)


def _map(role: str, payload: Any) -> Mapping[str, bool] | None:
    """Платформенный OnboardingStatusResponse → `done_flag`-и автомата flow_data.json.

    Флаги за пределами publish-цепочки платформы (owner: egrn_verified/payout_saved;
    tenant: solvency_confirmed) платформа в onboarding-статусе НЕ отдаёт — они опущены →
    flow трактует их как незавершённые (гид доведёт до них). Расширение — контракт #16.
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
        }
    # tenant: profile-минимум для брони = профиль И телефон подтверждены
    return {
        "account": True,
        "profile_complete": done.get("profile", False) and done.get("phone", False),
        "kyc_passed": done.get("kyc", False),
    }
