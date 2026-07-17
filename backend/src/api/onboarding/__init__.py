"""Онбординг-конфигурация Консьержа — детерминированный драйвер к полной верификации.

O0.0 (walking skeleton, этот пакет): чистый автомат «один экран за раз» поверх
self-scoped status-reads платформы (документ «План онбординг-агента v1.1», §2/§3).
Выбор экрана — детерминированно из состояния (enum→screen), не AI (G-deterministic-route).
Реализован ПО ОБРАЗЦУ `api/orders` (flow-логика + редактируемый `*_data.json`).

Персистентная OnboardingState (резюмируемость из памяти слоя отношения), действия в
сессии (deep-link/render_screen за CC-1), auto-fill/inline-валидация — следующими срезами
(фаза O1, за общими гейтами CC-1/боевизация/event-source). Потолок верификации
(KYC/solvency/ЕГРН/деньги — платформа/человек) неизменен.
"""

from __future__ import annotations

from api.onboarding.constants import ONBOARDING_ROLES, ROLE_OWNER, ROLE_TENANT
from api.onboarding.flow import (
    OnboardingStep,
    completed_step_ids,
    is_complete,
    next_step,
    progress,
    step_for_blocker,
    steps_for,
)

__all__ = [
    "ONBOARDING_ROLES",
    "OnboardingStep",
    "ROLE_OWNER",
    "ROLE_TENANT",
    "completed_step_ids",
    "is_complete",
    "next_step",
    "progress",
    "step_for_blocker",
    "steps_for",
]
