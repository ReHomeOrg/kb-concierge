"""Seam чтения статуса верификации для онбординг-гида.

Онбординг-гид детерминирован, но нуждается в СТАТУСЕ шагов пользователя (KYC пройден?
объект добавлен? …). Боевое чтение — делегированное (агент ОТ ИМЕНИ пользователя,
G7) обращение к платформе — за гейтами CC-1 + контракт владельца (#16). Здесь —
интерфейс `OnboardingStatusReader` + `NullStatusReader` (деградация FR-6.6): пока
реального чтения нет, статус неизвестен (`None`) → гид работает в режиме ПУТИ.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from api.tools.base import ToolContext


class OnboardingStatusReader(Protocol):
    """Источник статус-флагов онбординга (self-scoped/делегированно). `None` — неизвестно."""

    async def read(self, role: str, context: ToolContext) -> Mapping[str, bool] | None: ...


class NullStatusReader:
    """Статус недоступен (до боевого делегированного чтения платформы). Всегда `None`.

    Гид деградирует в режим ПУТИ (показывает шаги, не утверждая позицию) — честно, без
    ложных утверждений о прогрессе. Заменяется боевым reader'ом при разблокировке CC-1/#16.
    """

    async def read(self, role: str, context: ToolContext) -> Mapping[str, bool] | None:
        return None
