"""Правило обработки аварийных ситуаций (документ «Консьерж: правила обработки заявок»).

Детерминированный плейбук поверх модулей (ADR-0001): при аварии внутри помещения по
контракту Консьерж даёт самый быстрый безопасный путь — немедленное действие + телефон
правильной службы (единые номера + УК из карточки + указатели; БЕЗ интернет-поиска), и
дополнительно предлагает оформить заявку сервис-партнёру (FR-7.4), честно предупреждая, что
это дольше. Общедомовое (лифт, дом-газ) — направляем к ответственной службе, без заявки.

Партнёрская заявка переиспользует REPAIR-флоу (`api/orders/`). Доменного состояния/FSM тут
нет — они у kb-partners.
"""

from __future__ import annotations

from api.emergency.constants import (
    PARTNER_CREATE,
    PARTNER_NONE,
    PARTNER_OFFER,
)
from api.emergency.playbook import (
    PlaybookEntry,
    build_emergency_message,
    classify_emergency,
    entry_for,
)

__all__ = [
    "PARTNER_CREATE",
    "PARTNER_NONE",
    "PARTNER_OFFER",
    "PlaybookEntry",
    "build_emergency_message",
    "classify_emergency",
    "entry_for",
]
