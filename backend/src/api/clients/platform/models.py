"""DTO контекста пользователя из rehome.one (read-only, §8).

**G1 (инвариант):** контекст НЕ несёт денежных полей (суммы/комиссии/escrow/цены) —
агент денег не считает и не видит их через этот инструмент. Маппинг провизорного
контракта (адаптер) берёт ТОЛЬКО разрешённые не-денежные поля (whitelist).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Premises:
    """Объект (помещение) пользователя — справочные данные, без денег."""

    premises_id: str
    address: str | None = None
    kind: str | None = None
    # Контакт аварийной/управляющей службы дома (телефон диспетчера УК) — для аварийного
    # плейбука (#аварии). Публичный контакт службы, не ПДн пользователя. None, если в
    # карточке нет (тогда — обобщённая формулировка). Forward-compatible: сосед может не отдавать.
    management_contact: str | None = None


@dataclass(frozen=True)
class Booking:
    """Бронь/договор пользователя — статус и сроки, БЕЗ сумм (G1)."""

    booking_id: str
    status: str | None = None
    start_date: str | None = None
    end_date: str | None = None


@dataclass(frozen=True)
class UserContext:
    """Контекст пользователя для агента (read-only, без денег).

    `unavailable=True` → rehome.one недоступен (деградация, FR-6.6).
    """

    user_id: str
    display_name: str | None = None
    premises: list[Premises] = field(default_factory=list)
    bookings: list[Booking] = field(default_factory=list)
    unavailable: bool = False
