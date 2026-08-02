"""Database engine/session.

SQLite runs under two writers (API + worker), so WAL mode and busy_timeout are
mandatory per §5 to avoid SQLITE_BUSY on concurrent writes.
"""

from __future__ import annotations

import os

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./chess_insights.db")

# check_same_thread=False so the engine can be shared across threads/processes.
# busy_timeout via connect_args guards against SQLITE_BUSY while a writer holds
# the lock.
_connect_args: dict[str, object] = {}
if DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False, "timeout": 5}

engine = create_engine(DATABASE_URL, connect_args=_connect_args)


def _set_sqlite_pragma(dbapi_connection: object, connection_record: object) -> None:
    """Enable WAL and a 5s busy timeout on every SQLite connection (§5)."""
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


# Attach the pragma to THIS engine only (not the global Engine class), so test
# suites or workers that spin up their own engines aren't affected. Only wired
# for SQLite; Postgres needs neither WAL nor busy_timeout.
if DATABASE_URL.startswith("sqlite"):
    event.listens_for(engine, "connect")(_set_sqlite_pragma)


def init_db() -> None:
    """Create tables if they don't exist."""
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)
