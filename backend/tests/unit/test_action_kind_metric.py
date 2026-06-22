"""Юнит-тесты свёртки итога хода в лейбл метрики/события (§11, M8).

Фиксируют контракт нового сигнала `no_answer`: он отделён от общего `degraded`
(разные операционные проблемы — дыры retrieval vs недоступность соседа) и НЕ
публикуется как бизнес-событие webhook (§10).
"""

from __future__ import annotations

from api.policy.enums import AgentActionKind
from api.reasoning.loop import LoopResult
from api.sessions.service import _action_kind
from api.webhooks.events import event_for_action_kind


def test_no_answer_kind_takes_priority_over_degraded() -> None:
    # INFO_QA без ответа из БЗ несёт degraded=True И no_answer=True → лейбл no_answer.
    res = LoopResult(reply="x", degraded=True, no_answer=True)
    assert _action_kind(res, AgentActionKind.ANSWER) == "no_answer"


def test_plain_degraded_stays_degraded() -> None:
    # Деградация без no_answer (напр. недоступность write-инструмента) → degraded.
    res = LoopResult(reply="x", degraded=True, no_answer=False)
    assert _action_kind(res, AgentActionKind.ANSWER) == "degraded"


def test_no_answer_not_published_as_webhook() -> None:
    # §10: no_answer — внутренний операционный сигнал, не бизнес-событие (не публикуем).
    assert event_for_action_kind("no_answer") is None
