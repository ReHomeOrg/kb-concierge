"""Fixture: прямой SQL к чужой таблице в нижнем/смешанном регистре (#28).

Должен быть detect'ен скриптом AT-001 (case-insensitive матч).
"""

from sqlalchemy import text


def get_user(session, user_id):
    # Нарушение: lowercase select from users (чужая таблица).
    return session.execute(text("select * from users where id = :id"), {"id": user_id})


def join_bookings(session):
    # Нарушение: смешанный регистр Join Bookings.
    return session.execute(text("select t.* from tickets t Join Bookings b on t.booking_id = b.id"))
