"""Снимок контекста диалога для эскалации (FR-7.1). ТОЛЬКО маскированный текст (G3).

Из реплик берётся `content_masked` — наружу (в тикет kb-support, в логи, в LLM)
идёт исключительно маскированная версия (инвариант ПДн). Снимок ограничен по размеру
(хвост диалога), `context_snapshot_ref` — нессылочный дескриптор без ПДн.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from api.sessions.models import AgentTurn

# Предел снимка (символы): хвост диалога. С запасом под лимит входа tool (16000).
_MAX_SNAPSHOT_CHARS = 12000


def build_context_snapshot(turns: Sequence[AgentTurn]) -> str:
    """Собрать маскированный снимок диалога (хвост при переполнении лимита).

    Каждая реплика — `РОЛЬ: <маскированный текст>`. Сырой `content` НЕ используется
    (G3): в тикет/логи попадает только `content_masked`.
    """
    lines = [f"{turn.role.value}: {turn.content_masked}" for turn in turns]
    text = "\n".join(lines)
    if len(text) > _MAX_SNAPSHOT_CHARS:
        # Хвост диалога важнее начала — отрезаем с головы.
        text = "…\n" + text[-_MAX_SNAPSHOT_CHARS:]
    return text or "(пустой диалог)"


def snapshot_ref(session_id: uuid.UUID, turn_count: int) -> str:
    """Нессылочный дескриптор отправленного снимка (без ПДн): сессия + размер."""
    return f"snapshot:{session_id}:{turn_count}"
