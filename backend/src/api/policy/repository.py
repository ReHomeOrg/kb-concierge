"""Доступ к хранилищу политик автономности (своя БД)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.policy.models import AutonomyPolicy


class PolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def get_active(self) -> AutonomyPolicy | None:
        """Активная политика (не более одной — partial unique)."""
        stmt = select(AutonomyPolicy).where(AutonomyPolicy.active.is_(True))
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> list[AutonomyPolicy]:
        stmt = select(AutonomyPolicy).order_by(AutonomyPolicy.created_at.desc())
        return list((await self._db.execute(stmt)).scalars().all())

    async def get(self, policy_id: uuid.UUID) -> AutonomyPolicy | None:
        return await self._db.get(AutonomyPolicy, policy_id)

    async def create(self, policy: AutonomyPolicy) -> AutonomyPolicy:
        self._db.add(policy)
        await self._db.flush()
        await self._db.refresh(policy)
        return policy

    async def refresh(self, policy: AutonomyPolicy) -> None:
        await self._db.refresh(policy)

    async def deactivate_all(self) -> None:
        """Снять активность со всех (перед активацией другой; уважает partial unique)."""
        for policy in await self.list_all():
            policy.active = False
        await self._db.flush()

    async def commit(self) -> None:
        await self._db.commit()
