"""Детерминированный роутер шага онбординга (O0.0 walking skeleton).

Чистая логика без сети/БД/сессий (golden-тестируемо): по роли и статус-флагам
(из self-scoped status-reads платформы) вычисляет СЛЕДУЮЩИЙ незавершённый шаг, чьи
предусловия выполнены — «ровно один экран за раз, ничего вперёд» (G-single-screen,
G-deterministic-route). Выбор экрана детерминирован (не AI). Определение автомата —
в редактируемом `flow_data.json` (паттерн `fields_data.json`), env-override
`KBC_ONBOARDING_FLOW_PATH` (битый override → встроенный, FR-6.6; битый встроенный → ошибка).

O0.0 НЕ содержит: персистентной OnboardingState (резюмируемость — из памяти слоя
отношения на O1), действий в сессии (deep-link/CC-1 — O1), auto-fill/inline-валидации.
Потолок неизменен: KYC/solvency/ЕГРН/деньги решает платформа/человек (G-verification-ceiling).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)
_BUNDLED_PATH = Path(__file__).with_name("flow_data.json")
#: env-переменная ops-override пути к определению автомата (необязательна).
_OVERRIDE_ENV = "KBC_ONBOARDING_FLOW_PATH"


@dataclass(frozen=True)
class OnboardingStep:
    """Шаг онбординга: целевое действие + экран + предусловия + флаг завершённости.

    `requires` — id шагов-предусловий (prerequisite-guard). `done_flag` — имя статус-флага,
    истинность которого означает завершённость шага. `blocker_reason` — значение
    `contract_blocker_reason`, разблокируемое этим шагом (hook C25), или None.
    """

    step_id: str
    role: str
    target_action: str
    screen_ref: str
    requires: tuple[str, ...]
    done_flag: str
    blocker_reason: str | None


def _parse(data: Any) -> dict[str, tuple[OnboardingStep, ...]]:
    """Провалидировать JSON и собрать автомат по ролям. Некорректная структура → ValueError."""
    if not isinstance(data, dict):
        raise ValueError("onboarding flow root must be an object")
    roles_raw = data.get("roles")
    if not isinstance(roles_raw, dict) or not roles_raw:
        raise ValueError("onboarding flow has no roles")
    flows: dict[str, tuple[OnboardingStep, ...]] = {}
    for role, spec in roles_raw.items():
        steps: list[OnboardingStep] = []
        seen_ids: set[str] = set()
        for s in spec["steps"]:
            step = OnboardingStep(
                step_id=str(s["step_id"]),
                role=str(role),
                target_action=str(s["target_action"]),
                screen_ref=str(s["screen_ref"]),
                requires=tuple(str(r) for r in s.get("requires", [])),
                done_flag=str(s["done_flag"]),
                blocker_reason=(str(s["blocker_reason"]) if s.get("blocker_reason") else None),
            )
            # Ссылочная целостность: каждое предусловие обязано ссылаться на ПРЕДШЕСТВУЮЩИЙ
            # шаг той же роли. Ловит несуществующие/forward/циклические requires разом —
            # иначе шаг стал бы вечно недостижимым → тупик/ложное завершение (нарушение
            # G-no-dead-end / G-no-fake-complete). Порядок массива = порядок опроса.
            for req in step.requires:
                if req not in seen_ids:
                    raise ValueError(
                        f"onboarding role {role}: step {step.step_id} requires "
                        f"unknown/forward step {req!r}"
                    )
            seen_ids.add(step.step_id)
            steps.append(step)
        if not steps:
            raise ValueError(f"onboarding role {role} has no steps")
        flows[str(role)] = tuple(steps)
    return flows


def load_flows(path: str | None = None) -> dict[str, tuple[OnboardingStep, ...]]:
    """Загрузить автомат: из `path` (ops-override) либо из встроенного файла.

    Битый override → предупреждение + откат на встроенный (FR-6.6). Битый встроенный →
    исключение (fail-fast).
    """
    if path:
        try:
            return _parse(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            _logger.warning("onboarding flow override invalid (%s) → встроенный", exc)
    return _parse(json.loads(_BUNDLED_PATH.read_text(encoding="utf-8")))


_DATA = load_flows(os.getenv(_OVERRIDE_ENV) or None)


def steps_for(role: str) -> tuple[OnboardingStep, ...]:
    """Шаги роли в порядке опроса (пусто, если роль неизвестна)."""
    return _DATA.get(role, ())


def _is_done(step: OnboardingStep, status: Mapping[str, bool]) -> bool:
    return bool(status.get(step.done_flag))


def completed_step_ids(role: str, status: Mapping[str, bool]) -> tuple[str, ...]:
    """ID завершённых шагов роли (по статус-флагам)."""
    return tuple(s.step_id for s in steps_for(role) if _is_done(s, status))


def next_step(role: str, status: Mapping[str, bool]) -> OnboardingStep | None:
    """Следующий незавершённый шаг, чьи предусловия выполнены (детерминированно).

    None → полная верификация достигнута (COMPLETE) ЛИБО роль неизвестна.
    prerequisite-guard: шаг с невыполненным `requires` не возвращается (нет тупика).
    Последовательность опроса держится ПОРЯДКОМ массива шагов; `requires` кодирует лишь
    жёсткие зависимости (напр. O4 «ЕГРН» → O3 «объект»), проверенные при загрузке.
    """
    done = set(completed_step_ids(role, status))
    for step in steps_for(role):
        if step.step_id in done:
            continue
        if all(req in done for req in step.requires):
            return step
    return None


def is_complete(role: str, status: Mapping[str, bool]) -> bool:
    """Все шаги роли завершены (полная верификация)."""
    steps = steps_for(role)
    return bool(steps) and all(_is_done(s, status) for s in steps)


def progress(role: str, status: Mapping[str, bool]) -> tuple[int, int]:
    """`(завершено, всего)` для прогресс-карты N4 («осталось N шагов»)."""
    steps = steps_for(role)
    return (sum(1 for s in steps if _is_done(s, status)), len(steps))


def step_for_blocker(role: str, blocker_reason: str) -> OnboardingStep | None:
    """Шаг, разблокирующий данный `contract_blocker_reason` (hook C25 «причина → фикс»)."""
    for step in steps_for(role):
        if step.blocker_reason == blocker_reason:
            return step
    return None
