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
AGENT_ORDER_STEPS = Counter(
    "agent_order_steps_total",
    "Шаги обработки заявки на услугу (правила R1+)",
    ["category", "action"],
)
AGENT_ORDER_MISSING_FIELDS = Counter(
    "agent_order_missing_fields_total",
    "Запрошенные обязательные поля заявки (R1/§3)",
    ["category", "field"],
)
AGENT_EMERGENCY = Counter(
    "agent_emergency_total",
    "Распознанные аварийные ситуации (плейбук): тип + режим партнёрской заявки",
    ["type", "partner_mode"],
)


def record_intent(intent: str, method: str) -> None:
    AGENT_INTENTS.labels(intent=intent, method=method).inc()


def record_policy(outcome: str, reason: str) -> None:
    AGENT_POLICY_DECISIONS.labels(outcome=outcome, reason=reason).inc()


def record_action(kind: str) -> None:
    """`kind` ∈ answered/clarify/handoff/awaiting_confirmation/action_taken/degraded."""
    AGENT_ACTIONS.labels(kind=kind).inc()


def record_handoff(trigger: str, status: str) -> None:
    AGENT_HANDOFFS.labels(trigger=trigger, status=status).inc()


def record_confirmation(verdict: str) -> None:
    """`verdict` ∈ requested/yes/no/unclear (§7.4)."""
    AGENT_CONFIRMATIONS.labels(verdict=verdict).inc()


def record_order_step(category: str, action: str) -> None:
    """Шаг обработки заявки. Лейблы — только enum/категория (низкая кардинальность, G3)."""
    AGENT_ORDER_STEPS.labels(category=category, action=action).inc()


def record_order_missing_field(category: str, field: str) -> None:
    """Запрос обязательного поля заявки (R1). Лейбл `field` — ключ из §3, не значение."""
    AGENT_ORDER_MISSING_FIELDS.labels(category=category, field=field).inc()


def record_emergency(type_: str, partner_mode: str) -> None:
    """Распознанная авария. Лейблы — только enum типа/режима (низкая кардинальность, G3)."""
    AGENT_EMERGENCY.labels(type=type_, partner_mode=partner_mode).inc()
