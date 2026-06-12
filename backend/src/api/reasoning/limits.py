"""Ограничения агентного цикла (bounded loop, FR-6.2).

Без лимитов агент мог бы зациклиться/исчерпать бюджет. Лимиты конфигурируемы;
бюджет токенов сейчас номинальный (seam) — реально считается с боевым LLM (ADR-0003).
"""

from __future__ import annotations

from dataclasses import dataclass

from api.config import Settings


@dataclass(frozen=True)
class Limits:
    """Лимиты на один ход диалога."""

    max_tool_calls: int = 3
    max_steps: int = 5
    token_budget: int = 4000  # номинальный (seam); используется с боевым LLM

    @classmethod
    def from_settings(cls, settings: Settings) -> Limits:
        return cls(
            max_tool_calls=settings.loop_max_tool_calls,
            max_steps=settings.loop_max_steps,
            token_budget=settings.loop_token_budget,
        )
