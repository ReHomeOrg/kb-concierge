"""Пользовательский гид онбординга (N5 копирайт · N4 прогресс · финал на ценности).

Чистая логика поверх FSM (`flow.py`): по роли и статус-флагам собирает ГИД —
человеческим языком (N5), с прогресс-картой (N4) и финалом НА ЦЕННОСТИ («можно
бронировать/листить»), не на «verified». Выбор шага детерминирован (не AI).

Статус `None` (неизвестен — напр. до боевого делегированного чтения платформы,
CC-1 + контракт #16) → режим ПУТИ: показать шаги к цели БЕЗ утверждения позиции
(честно: не знаем, где пользователь). Известный статус → утверждаем текущий шаг.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from api.onboarding.flow import (
    is_complete,
    next_step,
    progress,
    step_for_blocker,
    steps_for,
)

# Финал НА ЦЕННОСТИ per роль (N5): указывает на разблокированное действие, не на «verified».
_FINALE: dict[str, tuple[str, str]] = {
    "tenant": ("Готово! Теперь можно бронировать", "Верификация пройдена — выбирайте жильё."),
    "owner": ("Готово! Объект можно листить", "Верификация пройдена — размещайте объект."),
}
# Режим ПУТИ (статус неизвестен): показываем шаги, не утверждая позицию.
_PATH_TITLE: dict[str, str] = {
    "tenant": "Несколько шагов до бронирования",
    "owner": "Несколько шагов до размещения объекта",
}
_PATH_WHY = "Пройдём их по одному — покажу, что нужно на каждом."


@dataclass(frozen=True)
class OnboardingGuide:
    """Пользовательский гид текущего шага онбординга (для UI/фронта).

    `known` — известен ли статус (False → режим пути). `complete` — полная верификация.
    `step_id`/`screen_ref` — текущий целевой шаг (None в режиме пути / при complete).
    `done`/`total` — прогресс (N4). `path` — все шаги роли `[(step_id, title)]` для карты.
    """

    role: str
    known: bool
    complete: bool
    title: str
    why: str
    step_id: str | None
    screen_ref: str | None
    done: int
    total: int
    blocker_reason: str | None = None
    path: tuple[tuple[str, str], ...] = field(default_factory=tuple)


def _path(role: str) -> tuple[tuple[str, str], ...]:
    return tuple((s.step_id, s.title or s.target_action) for s in steps_for(role))


def build_guide(role: str, status: Mapping[str, bool] | None) -> OnboardingGuide | None:
    """Собрать гид по роли и статусу. `None` — неизвестная роль (вне онбординга)."""
    steps = steps_for(role)
    if not steps:
        return None
    path = _path(role)
    total = len(steps)

    if status is None:
        # Статус неизвестен → режим ПУТИ (не утверждаем позицию).
        return OnboardingGuide(
            role=role,
            known=False,
            complete=False,
            title=_PATH_TITLE.get(role, "Пройдём верификацию"),
            why=_PATH_WHY,
            step_id=None,
            screen_ref=None,
            done=0,
            total=total,
            path=path,
        )

    done, _ = progress(role, status)
    if is_complete(role, status):
        ftitle, fwhy = _FINALE.get(role, ("Готово!", ""))
        return OnboardingGuide(
            role=role,
            known=True,
            complete=True,
            title=ftitle,
            why=fwhy,
            step_id=None,
            screen_ref=None,
            done=done,
            total=total,
            path=path,
        )

    step = next_step(role, status)
    if step is None:  # защитно: не complete, но шага нет (недостижимо при валидном автомате)
        return None
    return OnboardingGuide(
        role=role,
        known=True,
        complete=False,
        title=step.title or step.target_action,
        why=step.why,
        step_id=step.step_id,
        screen_ref=step.screen_ref,
        done=done,
        total=total,
        path=path,
    )


def guide_for_blocker(
    role: str, blocker_reason: str, status: Mapping[str, bool] | None
) -> OnboardingGuide | None:
    """Гид разблокировки (C25 «причина → фикс»): навести на шаг, снимающий blocker.

    Blocker не сопоставлен шагу роли → обычный гид (`build_guide`). Иначе — гид того шага
    с проставленным `blocker_reason` (UI покажет причину + экран фикса на месте).
    """
    step = step_for_blocker(role, blocker_reason)
    if step is None:
        return build_guide(role, status)
    done, total = progress(role, status) if status is not None else (0, len(steps_for(role)))
    return OnboardingGuide(
        role=role,
        known=status is not None,
        complete=False,
        title=step.title or step.target_action,
        why=step.why,
        step_id=step.step_id,
        screen_ref=step.screen_ref,
        done=done,
        total=total,
        blocker_reason=blocker_reason,
        path=_path(role),
    )
