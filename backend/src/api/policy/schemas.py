"""Pydantic-схемы управления политикой автономности (§10)."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from api.policy.models import AutonomyPolicy


class PolicyRead(BaseModel):
    id: uuid.UUID
    version: str
    active: bool
    confidence_threshold: float
    rules: dict[str, Any] | None
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @classmethod
    def from_orm_policy(cls, policy: AutonomyPolicy) -> PolicyRead:
        return cls(
            id=policy.id,
            version=policy.version,
            active=policy.active,
            confidence_threshold=policy.confidence_threshold,
            rules=policy.rules,
            created_at=policy.created_at,
            updated_at=policy.updated_at,
        )


class PolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=64)
    confidence_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    rules: dict[str, Any] | None = None
    active: bool = False


class PolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    rules: dict[str, Any] | None = None
