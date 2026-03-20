"""Tests for the database module."""

from unittest.mock import MagicMock

import pytest

from app.core.database import (
    close_engine,
    create_sync_url,
    get_session_factory,
    init_engine,
)


def test_create_sync_url() -> None:
    """Sync URL uses the sqlite:/// scheme."""
    result = create_sync_url("db/tinker.db")
    assert result == "sqlite:///db/tinker.db"


def test_create_sync_url_absolute_path() -> None:
    """Sync URL preserves absolute paths."""
    result = create_sync_url("/var/lib/tinker/db/tinker.db")
    assert result == "sqlite:////var/lib/tinker/db/tinker.db"


@pytest.mark.asyncio
async def test_init_engine_returns_async_engine(tmp_path: object) -> None:
    """init_engine returns an AsyncEngine with the correct URL."""
    db_path = f"{tmp_path}/test.db"
    engine = init_engine(db_path)
    try:
        assert str(engine.url) == f"sqlite+aiosqlite:///{db_path}"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_init_engine_registers_pragma_listener(tmp_path: object) -> None:
    """init_engine registers the PRAGMA event listener on the sync engine."""
    from app.core.database import _set_sqlite_pragmas

    db_path = f"{tmp_path}/test.db"
    engine = init_engine(db_path)
    try:
        # Check that the 'connect' event has listeners on the sync engine
        from sqlalchemy import event

        has_listeners = event.contains(engine.sync_engine, "connect", _set_sqlite_pragmas)
        assert has_listeners is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_session_factory_produces_sessions(tmp_path: object) -> None:
    """get_session_factory returns a callable that creates AsyncSession instances."""
    db_path = f"{tmp_path}/test.db"
    engine = init_engine(db_path)
    try:
        factory = get_session_factory(engine)
        # The factory should be callable
        assert callable(factory)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_close_engine_disposes(tmp_path: object) -> None:
    """close_engine disposes the engine without error."""
    db_path = f"{tmp_path}/test.db"
    engine = init_engine(db_path)
    # Should not raise
    await close_engine(engine)


def test_sqlite_pragmas_applied() -> None:
    """_set_sqlite_pragmas executes WAL and busy_timeout on sqlite3 connections."""
    import sqlite3

    from app.core.database import _set_sqlite_pragmas

    conn = sqlite3.connect(":memory:")
    _set_sqlite_pragmas(conn, MagicMock())

    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode")
    journal_mode = cursor.fetchone()[0]
    # In-memory databases may report 'memory' instead of 'wal',
    # but the pragma should execute without error
    assert journal_mode in ("wal", "memory")

    cursor.execute("PRAGMA busy_timeout")
    busy_timeout = cursor.fetchone()[0]
    assert busy_timeout == 5000

    cursor.close()
    conn.close()


def test_sqlite_pragmas_ignores_non_sqlite() -> None:
    """_set_sqlite_pragmas does nothing for non-sqlite3 connections."""
    from app.core.database import _set_sqlite_pragmas

    mock_conn = MagicMock()
    # Should not raise or call anything meaningful
    _set_sqlite_pragmas(mock_conn, MagicMock())
    mock_conn.cursor.assert_not_called()
