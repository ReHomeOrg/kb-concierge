"""Fixture: SQL к СВОИМ таблицам kb-concierge — НЕ нарушение (#28).

`agent_sessions`/`agent_turns`/`audit_log` принадлежат kb-concierge, поэтому прямые
запросы к ним легитимны и НЕ должны триггерить AT-001 (в т.ч. lowercase).
"""

from sqlalchemy import text


def queries(session):
    session.execute(text("select * from agent_sessions where status = 'OPEN'"))
    session.execute(text("SELECT * FROM agent_turns WHERE session_id = :id"))
    session.execute(text("update audit_log set actor_id = :a where id = :id"))
