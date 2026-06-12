"""Сигналы хода для политики автономности (§7.1): деньги/претензия/необратимость/
чувствительность. Извлекаются из МАСКИРОВАННОГО текста (G3) эвристиками по ключевым
словам (как rules в intent-движке); полнота уточняется eval-набором.

Сигналы независимы от намерения и служат жёсткими триггерами handoff (G1/G5/G6).
"""

from __future__ import annotations

from dataclasses import dataclass

# Деньги/выплаты — действие с финансовыми последствиями (G1: никогда автономно).
_MONEY = (
    "оплат",
    "перевод денег",
    "перечисл",
    "выплат",
    "верните деньги",
    "вернуть деньги",
    "возврат денег",
    "комисси",
    "предоплат",
    "счёт на оплату",
    "депозит",
)
# Претензия/спор/гарантия — обязательный human-handoff (§7.1).
_CLAIM = (
    "претензи",
    "жалоб",
    "пожаловат",
    "спор",
    "компенсаци",
    "гаранти",
    "страхов",
    "ущерб",
    "обман",
    "некачествен",
)
# Необратимое действие (G5: только через инструмент модуля, не автономно вне матрицы).
_IRREVERSIBLE = (
    "отмен",
    "расторг",
    "закрыть претензи",
    "изменить договор",
    "удалить аккаунт",
    "отказ от заказа",
)
# Юридически/финансово чувствительный вопрос (INFO_QA → handoff).
_SENSITIVE = ("суд", "юрист", "иск ", "неустойк", "штраф", "налог", "судебн")


@dataclass(frozen=True)
class TurnSignals:
    """Жёсткие сигналы хода, влияющие на решение независимо от намерения."""

    money: bool = False
    claim_or_dispute: bool = False
    irreversible: bool = False
    sensitive: bool = False


def _any(text: str, needles: tuple[str, ...]) -> bool:
    return any(n in text for n in needles)


def extract_signals(masked_text: str) -> TurnSignals:
    """Извлечь сигналы из маскированного текста (G3)."""
    text = masked_text.lower()
    return TurnSignals(
        money=_any(text, _MONEY),
        claim_or_dispute=_any(text, _CLAIM),
        irreversible=_any(text, _IRREVERSIBLE),
        sensitive=_any(text, _SENSITIVE),
    )
