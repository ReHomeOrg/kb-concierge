"""Fixture: прямой SQL к чужой таблице.

Должен быть detect'ен скриптом AT-001.
"""

from sqlalchemy import text


def get_user_by_id(session, user_id):
    # Нарушение: прямой SELECT FROM users (чужая таблица rehome.one).
    return session.execute(text("SELECT * FROM users WHERE id = :id"), {"id": user_id})


def join_with_premises(session):
    # Нарушение: JOIN с чужой таблицей premises.
    return session.execute(text("SELECT t.* FROM tickets t JOIN premises p ON t.premises_id = p.id"))
