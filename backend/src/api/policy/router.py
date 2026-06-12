"""HTTP-роутер управления политикой автономности (§10). Монтируется под /api/v1/concierge.

Просмотр — staff (оператор/админ); изменение/создание — только админ. Активной может
быть не более одной политики (активация снимает активность с остальных).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status

from api.errors import ProblemException
from api.policy.dependencies import get_policy_repository, require_staff, require_staff_admin
from api.policy.models import AutonomyPolicy
from api.policy.repository import PolicyRepository
from api.policy.schemas import PolicyCreate, PolicyRead, PolicyUpdate

router = APIRouter(prefix="/policies", tags=["Policies"])


@router.get("", response_model=list[PolicyRead], summary="Список политик автономности (staff)")
async def list_policies(
    _: object = Depends(require_staff),
    repo: PolicyRepository = Depends(get_policy_repository),
) -> list[PolicyRead]:
    return [PolicyRead.from_orm_policy(p) for p in await repo.list_all()]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=PolicyRead,
    summary="Создать политику автономности (admin)",
)
async def create_policy(
    payload: PolicyCreate,
    _: object = Depends(require_staff_admin),
    repo: PolicyRepository = Depends(get_policy_repository),
) -> PolicyRead:
    if payload.active:
        await repo.deactivate_all()
    policy = await repo.create(
        AutonomyPolicy(
            version=payload.version,
            active=payload.active,
            confidence_threshold=payload.confidence_threshold,
            rules=payload.rules,
        )
    )
    await repo.commit()
    return PolicyRead.from_orm_policy(policy)


@router.patch(
    "/{policy_id}",
    response_model=PolicyRead,
    summary="Изменить политику (порог/правила/активность) — admin",
)
async def update_policy(
    policy_id: uuid.UUID,
    payload: PolicyUpdate,
    _: object = Depends(require_staff_admin),
    repo: PolicyRepository = Depends(get_policy_repository),
) -> PolicyRead:
    policy = await repo.get(policy_id)
    if policy is None:
        raise ProblemException.not_found(detail="Policy not found")
    if payload.active is True and not policy.active:
        await repo.deactivate_all()
    if payload.active is not None:
        policy.active = payload.active
    if payload.confidence_threshold is not None:
        policy.confidence_threshold = payload.confidence_threshold
    if payload.rules is not None:
        policy.rules = payload.rules
    await repo.commit()
    await repo.refresh(policy)
    return PolicyRead.from_orm_policy(policy)
