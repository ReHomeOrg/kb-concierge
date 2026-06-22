"""Prometheus-метрики решений/действий агента (наблюдаемость §11, M8).

Неймспейс `agent_*` — поведение оркестратора (намерения, решения политики,
действия, эскалации, подтверждения, деградации). Лейблы — стабильные enum-значения
(низкая кардинальность, без ПДн, G3). Отдельно от `http_*` (сервер) и
`external_client_*` (вызовы соседей). Экспонируются на `/metrics`.
"""

from __future__ import annotations

from prometheus_client import Counter

AGENT_INTENTS = Counter(
    "agent_intents_total",
    "Распознанные намерения обращений (§5)",
    ["intent", "method"],
)
AGENT_POLICY_DECISIONS = Counter(
    "agent_policy_decisions_total",
    "Решения политики автономности (§7)",
    ["outcome", "reason"],
)
AGENT_ACTIONS = Counter(
    "agent_actions_total",
    "Действия агента по итогу хода (§6.1)",
    ["kind"],
)
AGENT_HANDOFFS = Counter(
    "agent_handoffs_total",
    "Эскалации человеку (§7.3)",
    ["trigger", "status"],
)
AGENT_CONFIRMATIONS = Counter(
    "agent_confirmations_total",
    "События подтверждения платных/необратимых действий (§7.4)",
    ["verdict"],
)


def record_intent(intent: str, method: str) -> None:
    AGENT_INTENTS.labels(intent=intent, method=method).inc()


def record_policy(outcome: str, reason: str) -> None:
    AGENT_POLICY_DECISIONS.labels(outcome=outcome, reason=reason).inc()


def record_action(kind: str) -> None:
    """`kind` ∈ answered/clarify/handoff/awaiting_confirmation/action_taken/no_answer/degraded.

    `no_answer` — INFO_QA не нашёл ответа в БЗ (сигнал дыр retrieval); отделён от общего
    `degraded` (недоступность соседа-модуля), т.к. это разные операционные проблемы.
    """
    AGENT_ACTIONS.labels(kind=kind).inc()


def record_handoff(trigger: str, status: str) -> None:
    AGENT_HANDOFFS.labels(trigger=trigger, status=status).inc()


def record_confirmation(verdict: str) -> None:
    """`verdict` ∈ requested/yes/no/unclear (§7.4)."""
    AGENT_CONFIRMATIONS.labels(verdict=verdict).inc()
