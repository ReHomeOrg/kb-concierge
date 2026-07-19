"""Проводка позиции онбординг-воронки в outcome-ledger (вариант A: единый источник).

Seam `OnboardingOutcomeRecorder` (по образцу `OnboardingStatusReader`): когда гид читает
статус пользователя, эта же позиция пишется в ledger — единый источник истины, без нового
межмодульного контракта. Config-gated (`outcome_ledger_enabled`). Пишет в СВОЙ ledger
kb-concierge (НЕ в статус платформы — граница G7 цела: наружу ничего не пишем).

Телеметрия НЕ должна ронять read-гид (FR-6.6/G6): любая ошибка записи глотается с логом.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Mapping

from api.config import Settings
from api.ledger.enums import OutcomeKind
from api.ledger.repository import LedgerRepository
from api.ledger.subject import pseudonymous_subject_key
from api.onboarding.outcome import outcome_from_status

logger = logging.getLogger(__name__)


class OnboardingOutcomeRecorder:
    """Записывает позицию онбординг-воронки в ledger (в рамках сессии гида)."""

    def __init__(self, ledger: LedgerRepository, settings: Settings) -> None:
        self._ledger = ledger
        self._settings = settings

    async def record(self, role: str, subject_raw: str, status: Mapping[str, bool]) -> None:
        """Зафиксировать позицию субъекта в воронке (прогресс либо завершение).

        No-op при выключенном ledger. `subject_raw` (user_id) псевдонимизируется (G3).
        Вызывать только при ИЗВЕСТНОМ статусе и наличии субъекта (анонимов не трекаем —
        нет стабильного ключа воронки). Ошибки глотаются: гид read-only, телеметрия его
        не роняет.
        """
        if not self._settings.outcome_ledger_enabled:
            return
        outcome = outcome_from_status(role, status)
        if outcome is None:
            return
        subject_key = pseudonymous_subject_key(subject_raw)
        now = datetime.datetime.now(datetime.UTC)
        try:
            if outcome.complete:
                await self._ledger.record_completion(
                    OutcomeKind.ONBOARDING,
                    subject_key,
                    now=now,
                    step_seq=outcome.step_seq,
                    role=role,
                )
            else:
                await self._ledger.record_progress(
                    OutcomeKind.ONBOARDING,
                    subject_key,
                    step=outcome.step_id or "",
                    step_seq=outcome.step_seq,
                    now=now,
                    settle_after_seconds=self._settings.outcome_settle_after_seconds,
                    role=role,
                    meta={"done": outcome.done},
                )
            await self._ledger.commit()
        except Exception:
            logger.exception(
                "onboarding.outcome_record_failed",
                extra={"role": role, "step_seq": outcome.step_seq},
            )
