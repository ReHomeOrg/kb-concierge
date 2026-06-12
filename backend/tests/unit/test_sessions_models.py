"""Юнит-тесты доменного ядра сессий (M1.1): контракт enum'ов и структура моделей.

Персистентность/видимость/маскирование проверяются интеграционно в M1.2+ (real-DB).
Здесь — стабильность строковых значений (контракт API/БД/аудит) и карта таблиц.
"""

from __future__ import annotations

import datetime
import uuid

from api.sessions.enums import AccessLevel, AuditAction, SessionStatus, TurnRole
from api.sessions.models import AgentSession, AgentTurn, AuditLog


def test_session_status_values_are_stable_contract() -> None:
    assert {s.value for s in SessionStatus} == {
        "ACTIVE",
        "HANDED_OFF",
        "CLOSED",
        "FORGOTTEN",
    }


def test_turn_role_values_are_stable_contract() -> None:
    assert {r.value for r in TurnRole} == {"USER", "AGENT", "OPERATOR", "TOOL", "SYSTEM"}


def test_audit_action_minimal_m1_set() -> None:
    # Минимальный набор M1; расширяется в M2+ (intent/handoff) без миграции (VARCHAR).
    assert {
        AuditAction.SESSION_CREATED,
        AuditAction.MESSAGE_RECEIVED,
        AuditAction.AGENT_RESPONDED,
        AuditAction.SESSION_FORGOTTEN,
    } <= set(AuditAction)


def test_access_level_includes_anon_and_owner_contours() -> None:
    assert AccessLevel.PUBLIC.value == "PUBLIC"
    assert AccessLevel.LOGGED.value == "LOGGED"


def test_enum_values_fit_varchar_length() -> None:
    # _ENUM_LEN = 32; любое значение enum должно помещаться (иначе truncation в БД).
    longest = max(
        len(m.value) for e in (SessionStatus, TurnRole, AuditAction, AccessLevel) for m in e
    )
    assert longest <= 32


def test_models_map_to_expected_tables() -> None:
    assert AgentSession.__tablename__ == "agent_sessions"
    assert AgentTurn.__tablename__ == "agent_turns"
    assert AuditLog.__tablename__ == "audit_log"


def test_turn_pairs_raw_and_masked_content_columns() -> None:
    # Инвариант ПДн (G3): сырой и маскированный текст — оба NOT NULL.
    cols = AgentTurn.__table__.columns
    assert cols["content"].nullable is False
    assert cols["content_masked"].nullable is False


def test_audit_actor_id_is_not_null() -> None:
    # Инвариант NFR-6: у каждой записи аудита есть актор.
    assert AuditLog.__table__.columns["actor_id"].nullable is False


def test_session_can_be_constructed_anonymous() -> None:
    # Анонимная сессия: user_id=None допустим; expires_at обязателен (ретенция).
    session = AgentSession(
        id=uuid.uuid4(),
        user_id=None,
        channel="ai_chat",
        status=SessionStatus.ACTIVE,
        access_level=AccessLevel.PUBLIC,
        expires_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
    assert session.user_id is None
    assert session.status is SessionStatus.ACTIVE
