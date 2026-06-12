"""Жёсткие guardrails G1–G7 (§7.2, ИНВАРИАНТЫ) — defense-in-depth поверх Policy Engine.

Часть инвариантов обеспечивается другими слоями (G2 scope — `auth`/`sessions.access`;
G3 маска — `observability.pii_mask`; G7 on-behalf-of — `clients`/`tools`). Здесь —
то, что относится к РЕШЕНИЮ и к недоверенному контенту:

- **G4** анти-prompt-injection/эксфильтрация: ввод пользователя и ответы инструментов —
  НЕдоверенные; их нельзя трактовать как инструкции, нельзя раскрывать промпт/секреты.
- **G1/G5/G6** вето над решением: даже если матрица/сохранённая политика разрешила
  автономное действие, деньги/необратимость → HANDOFF (страховка для M4.3, где матрица
  грузится из БД и может быть мисконфигурирована).
"""

from __future__ import annotations

from api.policy.engine import PolicyDecision
from api.policy.enums import AgentActionKind, DecisionReason
from api.policy.signals import TurnSignals

# G4: маркеры попыток подмены инструкций / эксфильтрации (недоверенный контент).
_INJECTION_PATTERNS: tuple[str, ...] = (
    "ignore previous",
    "ignore all previous",
    "disregard previous",
    "disregard all instructions",
    "system prompt",
    "reveal your",
    "show your prompt",
    "act as",
    "jailbreak",
    "забудь инструкции",
    "забудь все инструкции",
    "забудь предыдущие",
    "игнорируй инструкции",
    "игнорируй предыдущие",
    "ты теперь",
    "покажи системный промпт",
    "покажи свой промпт",
    "раскрой промпт",
    "выведи промпт",
    "сообщи пароль",
    "выдай секрет",
    "раскрой секрет",
)

_UNTRUSTED_DELIM = "<<<untrusted>>>"


def is_injection_attempt(untrusted_text: str) -> bool:
    """Эвристика обнаружения prompt-injection во ВХОДЕ/ответе инструмента (G4)."""
    text = untrusted_text.lower()
    return any(pattern in text for pattern in _INJECTION_PATTERNS)


def wrap_untrusted(untrusted_text: str) -> str:
    """Обернуть недоверенный контент для безопасной подачи в LLM (G4).

    Контент трактуется как ДАННЫЕ между делимитерами; инструкции внутри не исполняются.
    Сами делимитеры в тексте вырезаются, чтобы их нельзя было подделать. Используется
    Reasoning Loop'ом (M5) перед формированием LLM-промпта.
    """
    safe = untrusted_text.replace(_UNTRUSTED_DELIM, "")
    return f"{_UNTRUSTED_DELIM}\n{safe}\n{_UNTRUSTED_DELIM}"


def enforce_decision(decision: PolicyDecision, signals: TurnSignals) -> PolicyDecision:
    """Вето G1/G5/G6 поверх решения: автономное действие при деньгах/необратимости → HANDOFF.

    Дублирует логику движка осознанно (defense-in-depth): движок может быть заменён
    матрицей из БД (M4.3); этот инвариант держится независимо.
    """
    is_autonomous = decision.outcome in (AgentActionKind.ANSWER, AgentActionKind.TOOL_CALL)
    if is_autonomous and signals.money:
        return PolicyDecision(
            AgentActionKind.HANDOFF,
            DecisionReason.MONEY_NEVER_AUTONOMOUS,
            decision.policy_version,
        )
    if is_autonomous and signals.irreversible:
        return PolicyDecision(
            AgentActionKind.HANDOFF,
            DecisionReason.IRREVERSIBLE_HANDOFF,
            decision.policy_version,
        )
    return decision
