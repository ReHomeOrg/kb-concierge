"""Юнит-тесты доменного ядра human-handoff (M6.1): контракт enum'ов и модель."""

from __future__ import annotations

import uuid

from api.handoff.enums import HandoffStatus, HandoffTrigger
from api.handoff.models import HandoffRecord
from api.sessions.enums import AuditAction


def test_handoff_trigger_values_are_stable_contract() -> None:
    assert {t.value for t in HandoffTrigger} == {"POLICY", "USER", "OPERATOR"}


def test_handoff_status_values_are_stable_contract() -> None:
    assert {s.value for s in HandoffStatus} == {"OPEN", "PENDING", "RESOLVED"}


def test_handoff_enum_values_fit_varchar_length() -> None:
    longest = max(len(m.value) for e in (HandoffTrigger, HandoffStatus) for m in e)
    assert longest <= 32


def test_m6_audit_actions_present() -> None:
    # Расширение аудита M6 хранится VARCHAR — миграции данных не требует.
    assert {AuditAction.HANDOFF_CREATED, AuditAction.OPERATOR_REPLIED} <= set(AuditAction)
    assert max(len(a.value) for a in AuditAction) <= 32


def test_handoff_record_maps_to_table() -> None:
    assert HandoffRecord.__tablename__ == "handoff_records"


def test_handoff_record_session_id_and_reason_not_null() -> None:
    cols = HandoffRecord.__table__.columns
    assert cols["session_id"].nullable is False
    assert cols["reason"].nullable is False
    # target_ref допускает NULL (status=PENDING при деградации kb-support, FR-6.6).
    assert cols["target_ref"].nullable is True


def test_handoff_record_can_be_constructed() -> None:
    rec = HandoffRecord(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        trigger=HandoffTrigger.POLICY,
        status=HandoffStatus.OPEN,
        reason="MONEY_NEVER_AUTONOMOUS",
        target_ref="T-1",
    )
    assert rec.trigger is HandoffTrigger.POLICY
    assert rec.target_ref == "T-1"
