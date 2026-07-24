"""Сигналы хода для политики автономности (§7.1): деньги/претензия/необратимость/
чувствительность. Извлекаются из МАСКИРОВАННОГО текста (G3) эвристиками по ключевым
словам (как rules в intent-движке); полнота уточняется eval-набором.

Сигналы независимы от намерения и служат жёсткими триггерами handoff (G1/G5/G6).
"""

from __future__ import annotations

from dataclasses import dataclass

# Денежное ДЕЙСТВИЕ — транзакция с финансовыми последствиями (G1: никогда автономно,
# всегда HANDOFF, в т.ч. вето defense-in-depth). Отделено от денежной ТЕМЫ (#51).
_MONEY_ACTION = (
    "оплат",
    "перевод денег",
    "перечисл",
    "выплат",
    "верните деньги",
    "вернуть деньги",
    "возврат денег",
    "предоплат",
    "счёт на оплату",
)
# Денежная ТЕМА (номинальные слова, #51): упоминание денежной сущности без транзакции.
# Не действие → под PRICING_QUERY допустимо (read-only цитата тарифа); прочие интенты →
# HANDOFF как раньше. Вето defense-in-depth ключится на `money_action`, не на тему.
_MONEY_TOPIC = (
    "комисси",
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
    """Жёсткие сигналы хода, влияющие на решение независимо от намерения.

    `money_action` — денежная транзакция (всегда HANDOFF, G1). `money_topic` — упоминание
    денежной сущности без транзакции (комиссия/депозит): HANDOFF везде, кроме PRICING_QUERY
    (там read-only цитата тарифа безопасна). `money` — их дизъюнкция (для трассы/совместимости).
    """

    money_action: bool = False
    money_topic: bool = False
    claim_or_dispute: bool = False
    irreversible: bool = False
    sensitive: bool = False

    @property
    def money(self) -> bool:
        """Любое упоминание денег (действие ИЛИ тема). Совместимость трассы/чтения."""
        return self.money_action or self.money_topic


def _any(text: str, needles: tuple[str, ...]) -> bool:
    return any(n in text for n in needles)


def extract_signals(masked_text: str) -> TurnSignals:
    """Извлечь сигналы из маскированного текста (G3)."""
    text = masked_text.lower()
    return TurnSignals(
        money_action=_any(text, _MONEY_ACTION),
        money_topic=_any(text, _MONEY_TOPIC),
        claim_or_dispute=_any(text, _CLAIM),
        irreversible=_any(text, _IRREVERSIBLE),
        sensitive=_any(text, _SENSITIVE),
    )
