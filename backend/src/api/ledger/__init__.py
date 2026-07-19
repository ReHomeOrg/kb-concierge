"""Outcome-ledger (L0 KPI-истина): фиксация исходов пути пользователя.

Своя таблица kb-concierge (арх-константа ADR-0001) — НЕ чужая БД. `subject_key`
псевдонимен (sha256, G3: без ПДн). Первый `kind` — онбординг: измеряем drop-off по
шагам обезличенно. Settle-воркер закрывает «повисшие» открытые записи как ABANDONED
после окна бездействия (иначе брошенный путь нельзя отличить от идущего).

Config-gated: `outcome_ledger_enabled=False` → settle-воркер инертен (no-op).
"""

from __future__ import annotations

from api.ledger.subject import pseudonymous_subject_key

__all__ = ["pseudonymous_subject_key"]
