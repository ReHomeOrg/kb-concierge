"""Константы домена обработки заявок (без импорта кода соседей — арх-константа ADR-0001).

Категории — значения группы B реестра kb-partners (строки, не импорт enum соседа).
Документ §3 описывает обязательные поля только для трёх; KEY_DELIVERY/OTHER в
order-flow не ведутся (обычное поведение / handoff).
"""

from __future__ import annotations

# Категории партнёрской услуги, у которых есть обязательные поля §3 (R1).
CATEGORY_CLEANING = "CLEANING"
CATEGORY_MOVING = "MOVING"
CATEGORY_REPAIR = "REPAIR"  # документный `home_repair`

ORDER_CATEGORIES: frozenset[str] = frozenset({CATEGORY_CLEANING, CATEGORY_MOVING, CATEGORY_REPAIR})

# Города обслуживания (R2/§3 moving — развести SPB/MSK). Нижний регистр — конвенция
# `service_area` реестра kb-partners.
CITY_SPB = "spb"
CITY_MSK = "msk"
