"""Константы онбординг-флоу (без импорта кода соседей — арх-константа ADR-0001).

O0.0: детерминированный автомат ведёт пользователя к ПОЛНОЙ ВЕРИФИКАЦИИ по
кратчайшему пути. Роли и статус-флаги — строки (конвенция статус-ридов платформы),
не импорт enum платформы. Blocker-reasons = значения `contract_blocker_reason`.
"""

from __future__ import annotations

# Роли онбординга (ветки автомата).
ROLE_TENANT = "tenant"  # арендатор → цель: готов бронировать
ROLE_OWNER = "owner"  # собственник → цель: готов листить объект

ONBOARDING_ROLES: frozenset[str] = frozenset({ROLE_TENANT, ROLE_OWNER})

# Статус-флаги завершённости шага (из self-scoped status-reads платформы).
FLAG_ACCOUNT = "account"  # сессия/аккаунт создан
FLAG_EMAIL_VERIFIED = "email_verified"  # e-mail подтверждён (недостающий контакт)
FLAG_PROFILE_COMPLETE = "profile_complete"  # профиль-минимум заполнен
FLAG_KYC_PASSED = "kyc_passed"  # верификация личности пройдена
FLAG_SOLVENCY_CONFIRMED = "solvency_confirmed"  # платёжеспособность подтверждена
FLAG_OBJECT_ADDED = "object_added"  # объект добавлен
FLAG_EGRN_VERIFIED = "egrn_verified"  # объект верифицирован (ЕГРН, Контур)
FLAG_PAYOUT_SAVED = "payout_saved"  # payout-реквизиты сохранены

# Blocker-reasons (значения contract_blocker_reason платформы) → шаг для разблокировки (C25).
BLOCKER_TENANT_PROFILE_INCOMPLETE = "TENANT_PROFILE_INCOMPLETE"
BLOCKER_SOLVENCY_NOT_CONFIRMED = "SOLVENCY_NOT_CONFIRMED"
BLOCKER_OWNER_KYC_REQUIRED = "OWNER_KYC_REQUIRED"
