"""Типы исходящих событий агента (§10) и сборка ПДн-безопасной нагрузки.

Подписчики получают факт события без содержимого диалога (G3): только
идентификаторы/перечисления/номера. Имена событий — стабильный контракт.
"""

from __future__ import annotations

import uuid
from typing import Any

# §10: исходящие webhooks агента.
INTENT_CLASSIFIED = "agent.intent_classified"
ANSWERED = "agent.answered"
CLARIFY_REQUESTED = "agent.clarify_requested"
ACTION_TAKEN = "agent.action_taken"
HANDOFF_CREATED = "agent.handoff_created"

# Свёртка итога хода (kind, см. agent_metrics) → имя события (или None — не публикуем).
_ACTION_EVENT: dict[str, str] = {
    "answered": ANSWERED,
    "clarify": CLARIFY_REQUESTED,
    "action_taken": ACTION_TAKEN,
}


def event_for_action_kind(kind: str) -> str | None:
    """Имя webhook-события для итога хода (handoff публикуется отдельно; прочее — нет)."""
    return _ACTION_EVENT.get(kind)


def intent_payload(
    *, session_id: uuid.UUID, intent: str, confidence: float | None, method: str
) -> dict[str, Any]:
    return {
        "session_id": str(session_id),
        "intent": intent,
        "confidence": confidence,
        "method": method,
    }


def action_payload(*, session_id: uuid.UUID, intent: str, kind: str) -> dict[str, Any]:
    return {"session_id": str(session_id), "intent": intent, "kind": kind}


def handoff_payload(
    *, session_id: uuid.UUID, trigger: str, status: str, ticket_ref: str | None
) -> dict[str, Any]:
    return {
        "session_id": str(session_id),
        "trigger": trigger,
        "status": status,
        "ticket_ref": ticket_ref,
    }
