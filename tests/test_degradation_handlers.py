"""Lab 10: a lost database node must become a 503, never an unhandled 500.

Found by stopping the control-plane primary under load: errors raised while a
pooled connection was already dead (``InternalClientError``, ``connection was
closed in the middle of operation``) and errors raised while *closing* the
session escaped as 500 / reset connections, which nginx counted against
``max_fails`` until both instances were out of rotation.
"""

from __future__ import annotations

import asyncpg.exceptions as pg_exc
import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, InternalError, OperationalError

from api.main import database_unavailable
from shared.consistency import CONNECTIVITY_ERRORS
from shared.db import db


def _dbapi(exc_type: type[DBAPIError], invalidated: bool = False) -> DBAPIError:
    err = exc_type("SELECT 1", {}, Exception("boom"))
    err.connection_invalidated = invalidated
    return err


@pytest.mark.parametrize(
    "exc",
    [
        _dbapi(InternalError),
        _dbapi(OperationalError),
        _dbapi(DBAPIError, invalidated=True),
        pg_exc.CannotConnectNowError("the database system is shutting down"),
        ConnectionRefusedError(),
        TimeoutError(),
    ],
)
async def test_lost_node_errors_become_503(exc) -> None:
    response = await database_unavailable(None, exc)  # type: ignore[arg-type]
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"


async def test_real_sql_errors_are_not_reported_as_unavailability() -> None:
    response = await database_unavailable(None, _dbapi(IntegrityError))  # type: ignore[arg-type]
    assert response.status_code == 500


def test_degraded_path_recognises_what_the_handler_recognises() -> None:
    for exc in (_dbapi(InternalError), pg_exc.CannotConnectNowError("down")):
        assert isinstance(exc, CONNECTIVITY_ERRORS)


async def test_session_close_survives_a_lost_connection(monkeypatch) -> None:
    async def broken_close(self) -> None:
        raise _dbapi(InternalError)

    monkeypatch.setattr(db.AsyncSession, "close", broken_close)
    session = db.TolerantSession()
    await session.close()  # must not raise


async def test_redis_outage_is_a_503() -> None:
    import redis.exceptions as redis_exc

    from api.main import buffer_unavailable

    response = await buffer_unavailable(None, redis_exc.ConnectionError("down"))  # type: ignore[arg-type]
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
