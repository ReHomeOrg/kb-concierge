"""DTO ответа kb-tariffs (расчёт канонических тарифов). Контракт изолирован в адаптере.

Числа держим строками (как их отдаёт kb-tariffs — Decimal сериализуется в строку):
точность не теряется по дороге к ответу, и агент цитирует значение дословно.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TariffSource:
    """Ссылка на канонический источник (для цитаты/объяснимости)."""

    title: str
    ref: str


@dataclass(frozen=True)
class Quote:
    """Результат расчёта тарифа. `unavailable=True` → сосед недоступен (деградация, FR-6.6).

    Все суммы/ставки — строки (округление и точность — на стороне kb-tariffs).
    """

    tariff_version: str = ""
    contract_year: int = 0
    side: str = ""
    commission_rate: str = ""
    commission_amount_rub: str = ""
    commission_applies_to: str = ""
    service_fee_rate: str = ""
    service_fee_amount_rub: str = ""
    service_fee_applies_to: str = ""
    lost_income_compensation_rub: str = ""
    lost_income_applies_to: str = ""
    insurance_coverage_rub: str = ""
    sources: list[TariffSource] = field(default_factory=list)
    unavailable: bool = False
