"""Database engine and session management for Tinker.

Provides async SQLAlchemy engine creation, session factory, and SQLite
PRAGMA configuration (WAL mode, busy_timeout).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


def _set_sqlite_pragmas(dbapi_conn: object, connection_record: object) -> None:
    """Set SQLite PRAGMAs on every new connection.

    Enables WAL mode for concurrent reads during writes and sets a busy
    timeout as a safety net for concurrent write attempts.

    Args:
        dbapi_conn: The raw DBAPI connection.
        connection_record: SQLAlchemy connection record (unused).
    """
    import sqlite3

    if isinstance(dbapi_conn, sqlite3.Connection):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


def init_engine(db_path: str) -> AsyncEngine:
    """Create and configure the async SQLAlchemy engine.

    Registers SQLite PRAGMA event listeners on the underlying sync engine
    so that WAL mode and busy_timeout are applied to every connection.

    Args:
        db_path: Path to the SQLite database file (e.g. ``db/tinker.db``).

    Returns:
        A configured ``AsyncEngine`` instance.
    """
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False)

    # Register PRAGMAs on the sync engine so they fire for every connection.
    sync_engine: Engine = engine.sync_engine
    event.listen(sync_engine, "connect", _set_sqlite_pragmas)

    return engine


def get_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the given engine.

    Args:
        engine: The ``AsyncEngine`` to bind sessions to.

    Returns:
        An ``async_sessionmaker`` that produces ``AsyncSession`` instances.
    """
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def close_engine(engine: AsyncEngine) -> None:
    """Dispose of the async engine, releasing all connections.

    Args:
        engine: The ``AsyncEngine`` to dispose.
    """
    await engine.dispose()


def create_sync_url(db_path: str) -> str:
    """Return a synchronous SQLite URL for Alembic migrations.

    Alembic requires a synchronous engine. This helper produces the
    correct ``sqlite:///`` URL from the same database path used by
    the async engine.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        A synchronous SQLite connection URL string.
    """
    return f"sqlite:///{db_path}"
