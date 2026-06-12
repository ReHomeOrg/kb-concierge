"""FastAPI-зависимости домена политики: RBAC (staff) + репозиторий."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import get_current_principal
from api.auth.principal import Principal
from api.db import get_session
from api.errors import ProblemException
from api.policy.repository import PolicyRepository


async def require_staff(principal: Principal = Depends(get_current_principal)) -> Principal:
    """Просмотр политик — оператор или админ (§10)."""
    if not (principal.is_operator or principal.is_staff_admin):
        raise ProblemException.forbidden(detail="Staff access required")
    return principal


async def require_staff_admin(principal: Principal = Depends(get_current_principal)) -> Principal:
    """Изменение политик — только админ (§10)."""
    if not principal.is_staff_admin:
        raise ProblemException.forbidden(detail="Staff admin access required")
    return principal


def get_policy_repository(db: AsyncSession = Depends(get_session)) -> PolicyRepository:
    return PolicyRepository(db)
