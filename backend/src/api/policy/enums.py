"""Перечисления слоя политики автономности (ТЗ §7).

Стабильный контракт (трасса решения / аудит). `AgentActionKind` — что агент делает
по итогу решения; `DecisionReason` — почему (объяснимость NFR-10).
"""

from __future__ import annotations

import enum


class AgentActionKind(str, enum.Enum):
    """Вид действия агента по решению политики (§6.1 AgentAction.type).

    ANSWER — ответить самому (из KB/шаблона). TOOL_CALL — вызвать инструмент модуля
    (создать заявку/тикет и т.п.). CLARIFY — уточнить у пользователя (мало данных/
    низкая уверенность). HANDOFF — передать человеку (эскалация).
    """

    ANSWER = "ANSWER"
    TOOL_CALL = "TOOL_CALL"
    CLARIFY = "CLARIFY"
    HANDOFF = "HANDOFF"


class DecisionReason(str, enum.Enum):
    """Причина решения политики (трасса/аудит, объяснимость)."""

    AUTONOMOUS_OK = "AUTONOMOUS_OK"  # действие разрешено матрицей §7.1
    PAID_NEEDS_CONFIRMATION = "PAID_NEEDS_CONFIRMATION"  # FR-7.4: подтверждение пользователя
    LOW_CONFIDENCE = "LOW_CONFIDENCE"  # ниже порога → уточнение (G6)
    MANDATORY_HANDOFF_CLAIM = "MANDATORY_HANDOFF_CLAIM"  # претензия/спор → человек
    MONEY_NEVER_AUTONOMOUS = "MONEY_NEVER_AUTONOMOUS"  # G1: деньги/выплаты — никогда сам
    IRREVERSIBLE_HANDOFF = "IRREVERSIBLE_HANDOFF"  # G5: необратимое вне матрицы → человек
    SENSITIVE_HANDOFF = "SENSITIVE_HANDOFF"  # юридически/финансово чувствительный вопрос
    NON_STANDARD_ESCALATION = "NON_STANDARD_ESCALATION"  # нестандарт → всегда эскалация
    SMALL_TALK = "SMALL_TALK"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
