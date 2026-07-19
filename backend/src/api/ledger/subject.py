"""Псевдонимизация субъекта пути для outcome-ledger (G3: без ПДн в таблице).

`subject_key` — стабильный необратимый ключ (sha256 hex) сырого идентификатора
(user_id/session_id). Одинаковый вход → одинаковый ключ (агрегация воронки по одному
субъекту), но по ключу нельзя восстановить исходный id. В ledger кладём ТОЛЬКО ключ.
"""

from __future__ import annotations

import hashlib

_SUBJECT_KEY_LEN = 64  # длина sha256 hex


def pseudonymous_subject_key(raw_id: str) -> str:
    """sha256-hex сырого идентификатора субъекта (детерминированно, необратимо).

    Пустой/пробельный вход → ValueError: пустой ключ схлопнул бы разных субъектов
    в одну запись и исказил бы воронку.
    """
    normalized = raw_id.strip()
    if not normalized:
        raise ValueError("subject id must be non-empty for pseudonymization")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
