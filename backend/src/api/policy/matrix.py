"""Матрица автономности §7.1 как ДАННЫЕ (дефолты ТЗ, подтверждены Архитектором §13.1).

Версионируется (`POLICY_VERSION`) для трассы решения (FR-5.4). Может переопределяться
сохранённой `AutonomyPolicy` (M4.3); здесь — встроенный фолбэк.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from api.intent.enums import Intent
from api.policy.enums import AgentActionKind

#: Версия встроенной матрицы (меняется при правке правил автономности).
POLICY_VERSION = "1.0"


@dataclass(frozen=True)
class IntentRule:
    """Правило автономности для одного намерения (§7.1).

    `autonomous` — действие, если уверенность достаточна и нет стоп-сигналов.
    `allowed_tools` — инструменты, разрешённые этому намерению (исполняет loop, M5).
    `requires_confirmation` — платное/необратимое → подтверждение пользователя (FR-7.4).
    `gated_by_confidence` — действие гейтится порогом (ниже → CLARIFY, G6).
    """

    autonomous: AgentActionKind
    allowed_tools: tuple[str, ...] = ()
    requires_confirmation: bool = False
    gated_by_confidence: bool = True


# Дефолты §7.1 (подтверждены §13.1). Write-инструменты (support.*/partners.*)
# исполняются под политикой начиная с M7; политика разрешает их уже сейчас.
DEFAULT_MATRIX: dict[Intent, IntentRule] = {
    # Отвечает из KB с цитатами; чувствительное/низкая уверенность → handoff/clarify.
    Intent.INFO_QA: IntentRule(
        autonomous=AgentActionKind.ANSWER,
        allowed_tools=("kb.search",),
    ),
    # Создаёт заявку + классификация + диспетч партнёру; платное/необратимое →
    # подтверждение пользователя (FR-7.4). `partners.dispatch` исполняется ТОЛЬКО
    # после согласия (R3, необратимо).
    Intent.PARTNER_SERVICE: IntentRule(
        autonomous=AgentActionKind.TOOL_CALL,
        allowed_tools=("partners.create_request", "partners.classify", "partners.dispatch"),
        requires_confirmation=True,
    ),
    # Заводит тикет / типовой ответ; ЛЮБАЯ претензия/деньги/спор → handoff (сигналы).
    Intent.SUPPORT_ISSUE: IntentRule(
        autonomous=AgentActionKind.TOOL_CALL,
        allowed_tools=("support.create_ticket",),
    ),
    # Статус своей заявки/обращения — read-only ответ (без денег, без подтверждения,
    # без порога): get_status по последней заявке сессии (#4).
    Intent.STATUS_QUERY: IntentRule(
        autonomous=AgentActionKind.ANSWER,
        allowed_tools=("partners.get_status", "support.get_status"),
        gated_by_confidence=False,
    ),
    # Нестандарт — агент не решает сам (всегда эскалация).
    Intent.NON_STANDARD: IntentRule(
        autonomous=AgentActionKind.HANDOFF,
        gated_by_confidence=False,
    ),
    # Small talk / вне области — низкорисковый прямой ответ, без порога.
    Intent.SMALL_TALK: IntentRule(
        autonomous=AgentActionKind.ANSWER,
        gated_by_confidence=False,
    ),
    Intent.OUT_OF_SCOPE: IntentRule(
        autonomous=AgentActionKind.ANSWER,
        gated_by_confidence=False,
    ),
}


@dataclass(frozen=True)
class AutonomyMatrix:
    """Матрица автономности: правила по намерениям + порог уверенности."""

    rules: dict[Intent, IntentRule] = field(default_factory=lambda: dict(DEFAULT_MATRIX))
    confidence_threshold: float = 0.75  # ТЗ Док.2 (CONFIDENCE_THRESHOLD)
    version: str = POLICY_VERSION

    def rule_for(self, intent: Intent) -> IntentRule:
        """Правило намерения; неизвестное → безопасный фолбэк (эскалация)."""
        return self.rules.get(intent, IntentRule(autonomous=AgentActionKind.HANDOFF))

    @classmethod
    def from_policy(
        cls,
        *,
        confidence_threshold: float,
        version: str,
        rules_json: dict[str, Any] | None,
    ) -> AutonomyMatrix:
        """Собрать матрицу из сохранённой политики (M4.3). NULL/битые правила → DEFAULT_MATRIX.

        Некорректное правило отдельного намерения деградирует в встроенный дефолт
        (безопасность важнее: не активируем мусорную автономию из БД).
        """
        if not rules_json:
            return cls(confidence_threshold=confidence_threshold, version=version)
        rules = dict(DEFAULT_MATRIX)
        for intent in Intent:
            spec = rules_json.get(intent.value)
            if not isinstance(spec, dict):
                continue
            try:
                rules[intent] = IntentRule(
                    autonomous=AgentActionKind(spec["autonomous"]),
                    allowed_tools=tuple(spec.get("allowed_tools", ())),
                    requires_confirmation=bool(spec.get("requires_confirmation", False)),
                    gated_by_confidence=bool(spec.get("gated_by_confidence", True)),
                )
            except (KeyError, ValueError):
                continue
        return cls(rules=rules, confidence_threshold=confidence_threshold, version=version)
