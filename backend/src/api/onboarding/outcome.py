"""Проекция статуса онбординга в позицию воронки для outcome-ledger (чистая логика).

По роли и статус-флагам (тем же, что питают гид) детерминированно вычисляет позицию
пути в воронке: текущий целевой шаг + его порядковый номер (1-based), либо признак
полной верификации. Источник истины — существующий FSM (`flow.py`), НЕ новый контракт:
воронка выводится из того же чтения статуса, что и `build_guide` (вариант A).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from api.onboarding.flow import (
    completed_step_ids,
    is_complete,
    next_step,
    steps_for,
)


@dataclass(frozen=True)
class OnboardingOutcome:
    """Позиция пути в воронке (для записи в ledger).

    `complete` — полная верификация достигнута. `step_id` — текущий целевой шаг (None при
    complete). `step_seq` — порядковый номер шага в потоке роли (1-based; при complete =
    число шагов). `done` — сколько шагов завершено (обезличенный счётчик в meta).
    """

    complete: bool
    step_id: str | None
    step_seq: int
    done: int


def outcome_from_status(role: str, status: Mapping[str, bool]) -> OnboardingOutcome | None:
    """Позиция воронки по роли и известному статусу. `None` — роль вне онбординга.

    Вызывается ТОЛЬКО при известном статусе (не `None`): в режиме ПУТИ позиция неизвестна,
    записывать нечего. `step_seq` = индекс текущего шага в `steps_for(role)` (1-based) —
    «drop-off на шаге N» = пути, застрявшие на N-м шаге.
    """
    steps = steps_for(role)
    if not steps:
        return None
    done = len(completed_step_ids(role, status))
    if is_complete(role, status):
        return OnboardingOutcome(complete=True, step_id=None, step_seq=len(steps), done=done)
    step = next_step(role, status)
    if step is None:  # pragma: no cover — защитно: не complete, но шага нет (недостижимо)
        return None
    seq = next(i for i, s in enumerate(steps, start=1) if s.step_id == step.step_id)
    return OnboardingOutcome(complete=False, step_id=step.step_id, step_seq=seq, done=done)
