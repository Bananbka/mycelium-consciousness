"""Lab 9 regression guards for the profiled root cause: round trips per request."""

from __future__ import annotations

from sqlalchemy import event

from api.deps import _load_user
from shared.db import db


async def test_auth_lookup_is_a_single_statement(session, clone_user) -> None:
    """User and profile come back in one statement (``joinedload``).

    ``selectinload`` issued a second ``SELECT`` for the one-to-one profile,
    which the lab 9 profile identified as part of the per-request overhead.
    """
    user_id = clone_user.id
    statements: list[str] = []
    sync_engine = session.bind.sync_engine

    def record(conn, cursor, statement, *_args):
        statements.append(statement)

    event.listen(sync_engine, "before_cursor_execute", record)
    try:
        session.expire_all()
        user = await _load_user(session, user_id)
    finally:
        event.remove(sync_engine, "before_cursor_execute", record)

    assert user is not None and user.profile is not None
    assert len([s for s in statements if s.lstrip().startswith("SELECT")]) == 1


def test_pool_pre_ping_is_opt_in() -> None:
    """A probe on every checkout is a round trip; it must be an explicit choice."""
    assert db.engine.pool._pre_ping is db.POOL_PRE_PING
