"""Интеграционные тесты outcome-ledger (real DB): прогресс/завершение + settle брошенных."""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.ledger.enums import OutcomeKind, OutcomeResult, OutcomeState
from api.ledger.models import OutcomeRecord
from api.ledger.repository import LedgerRepository
from api.ledger.settle import process_settle_batch
from api.observability.agent_metrics import AGENT_OUTCOMES

pytestmark = pytest.mark.asyncio

_NOW = datetime.datetime(2030, 1, 1, tzinfo=datetime.UTC)
_WINDOW = 3600.0  # 1 час окна бездействия


async def _get(session: AsyncSession, subject: str) -> OutcomeRecord | None:
    stmt = select(OutcomeRecord).where(OutcomeRecord.subject_key == subject)
    return (await session.execute(stmt)).scalars().first()


async def test_record_progress_creates_open_record(session: AsyncSession) -> None:
    repo = LedgerRepository(session)
    rec = await repo.record_progress(
        OutcomeKind.ONBOARDING,
        "subj-a",
        step="profile",
        step_seq=1,
        now=_NOW,
        settle_after_seconds=_WINDOW,
        role="tenant",
    )
    await session.flush()
    assert rec.state == OutcomeState.OPEN
    assert rec.result is None
    assert rec.furthest_step == "profile"
    assert rec.step_seq == 1
    assert rec.role == "tenant"
    assert rec.settle_after == _NOW + datetime.timedelta(seconds=_WINDOW)


async def test_progress_advances_only_forward_but_resets_timer(session: AsyncSession) -> None:
    repo = LedgerRepository(session)
    await repo.record_progress(
        OutcomeKind.ONBOARDING,
        "subj-b",
        step="s1",
        step_seq=1,
        now=_NOW,
        settle_after_seconds=_WINDOW,
    )
    await repo.record_progress(
        OutcomeKind.ONBOARDING,
        "subj-b",
        step="s3",
        step_seq=3,
        now=_NOW,
        settle_after_seconds=_WINDOW,
    )
    later = _NOW + datetime.timedelta(minutes=10)
    # Возврат на ранний шаг НЕ откатывает воронку, но активность сдвигает settle_after.
    rec = await repo.record_progress(
        OutcomeKind.ONBOARDING,
        "subj-b",
        step="s2",
        step_seq=2,
        now=later,
        settle_after_seconds=_WINDOW,
    )
    await session.flush()
    assert rec.furthest_step == "s3"
    assert rec.step_seq == 3
    assert rec.settle_after == later + datetime.timedelta(seconds=_WINDOW)
    # Только одна OPEN-запись на субъект (частичный уникальный индекс).
    rows = (
        (await session.execute(select(OutcomeRecord).where(OutcomeRecord.subject_key == "subj-b")))
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_progress_equal_step_seq_does_not_advance(session: AsyncSession) -> None:
    """Граница forward-only (`step_seq > record.step_seq`): равный seq НЕ двигает воронку."""
    repo = LedgerRepository(session)
    await repo.record_progress(
        OutcomeKind.ONBOARDING,
        "subj-eq",
        step="s2",
        step_seq=2,
        now=_NOW,
        settle_after_seconds=_WINDOW,
    )
    rec = await repo.record_progress(
        OutcomeKind.ONBOARDING,
        "subj-eq",
        step="other-s2",
        step_seq=2,
        now=_NOW,
        settle_after_seconds=_WINDOW,
    )
    await session.flush()
    assert rec.furthest_step == "s2"  # равный seq не перезаписал шаг
    assert rec.step_seq == 2


async def test_progress_merges_meta(session: AsyncSession) -> None:
    """`meta` накапливается поверх (обезличенные счётчики), не затирается целиком."""
    repo = LedgerRepository(session)
    await repo.record_progress(
        OutcomeKind.ONBOARDING,
        "subj-meta",
        step="s1",
        step_seq=1,
        now=_NOW,
        settle_after_seconds=_WINDOW,
        meta={"steps_seen": 1, "channel": "web"},
    )
    rec = await repo.record_progress(
        OutcomeKind.ONBOARDING,
        "subj-meta",
        step="s2",
        step_seq=2,
        now=_NOW,
        settle_after_seconds=_WINDOW,
        meta={"steps_seen": 2},
    )
    await session.flush()
    assert rec.meta == {
        "steps_seen": 2,
        "channel": "web",
    }  # merge: обновил счётчик, сохранил channel


async def test_record_completion_settles_completed(session: AsyncSession) -> None:
    repo = LedgerRepository(session)
    await repo.record_progress(
        OutcomeKind.ONBOARDING,
        "subj-c",
        step="s1",
        step_seq=1,
        now=_NOW,
        settle_after_seconds=_WINDOW,
    )
    rec = await repo.record_completion(
        OutcomeKind.ONBOARDING, "subj-c", now=_NOW, step="done", step_seq=9
    )
    await session.flush()
    assert rec.state == OutcomeState.SETTLED
    assert rec.result == OutcomeResult.COMPLETED
    assert rec.settled_at == _NOW
    assert rec.furthest_step == "done"


async def test_completion_without_prior_progress_creates_settled(session: AsyncSession) -> None:
    repo = LedgerRepository(session)
    rec = await repo.record_completion(OutcomeKind.ONBOARDING, "subj-d", now=_NOW)
    await session.flush()
    assert rec.state == OutcomeState.SETTLED
    assert rec.result == OutcomeResult.COMPLETED


async def test_claim_settlable_respects_window_and_state(session: AsyncSession) -> None:
    repo = LedgerRepository(session)
    # Протухший OPEN (settle_after в прошлом) — кандидат.
    past = _NOW - datetime.timedelta(hours=2)
    await repo.record_progress(
        OutcomeKind.ONBOARDING,
        "subj-stale",
        step="s1",
        step_seq=1,
        now=past,
        settle_after_seconds=1.0,
    )
    # Свежий OPEN (settle_after в будущем) — НЕ кандидат.
    await repo.record_progress(
        OutcomeKind.ONBOARDING,
        "subj-fresh",
        step="s1",
        step_seq=1,
        now=_NOW,
        settle_after_seconds=_WINDOW,
    )
    # Уже завершённый — НЕ кандидат.
    await repo.record_completion(OutcomeKind.ONBOARDING, "subj-done", now=_NOW)
    await session.flush()

    claimed = await repo.claim_settlable(now=_NOW, limit=100)
    keys = {r.subject_key for r in claimed}
    assert "subj-stale" in keys
    assert "subj-fresh" not in keys
    assert "subj-done" not in keys


async def test_process_settle_batch_marks_abandoned_and_counts(session: AsyncSession) -> None:
    repo = LedgerRepository(session)
    past = _NOW - datetime.timedelta(hours=2)
    for subj in ("aband-1", "aband-2"):
        await repo.record_progress(
            OutcomeKind.ONBOARDING, subj, step="s1", step_seq=1, now=past, settle_after_seconds=1.0
        )
    await session.flush()

    before = AGENT_OUTCOMES.labels(
        kind=OutcomeKind.ONBOARDING.value, result=OutcomeResult.ABANDONED.value
    )._value.get()
    result = await process_settle_batch(repo=repo, now=_NOW, limit=100)
    assert result == {"claimed": 2, "abandoned": 2}
    after = AGENT_OUTCOMES.labels(
        kind=OutcomeKind.ONBOARDING.value, result=OutcomeResult.ABANDONED.value
    )._value.get()
    assert after == before + 2

    rec = await _get(session, "aband-1")
    assert rec is not None
    assert rec.state == OutcomeState.SETTLED
    assert rec.result == OutcomeResult.ABANDONED
    assert rec.settled_at == _NOW
