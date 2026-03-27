"""Shared test fixtures for Tinker."""

import logging
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from quart import Quart
from sqlalchemy.ext.asyncio import AsyncSession

from app import create_app

# Silence SQLAlchemy's pool logger at ERROR level.  When aiosqlite connections
# are GC'd after the per-test event loop closes, SQLAlchemy calls both
# util.warn() (handled by pytest's filterwarnings) *and* pool.logger.error()
# (bypasses filterwarnings, writes directly to stderr).  Raising the logger's
# effective level to CRITICAL suppresses the pool error path without affecting
# any other SQLAlchemy diagnostics.
logging.getLogger("sqlalchemy.pool").setLevel(logging.CRITICAL)


@pytest.fixture(autouse=True)
def _test_env(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set environment variables for testing."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("TINKER_DOMAIN", "test.example.com")
    monkeypatch.setenv("TINKER_DB_PATH", db_path)
    monkeypatch.setenv("TINKER_MEDIA_PATH", str(tmp_path / "media"))
    monkeypatch.setenv("TINKER_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TINKER_USERNAME", "testuser")


@pytest.fixture
async def app() -> AsyncGenerator[Quart, None]:
    """Create a test application instance with an initialised database schema."""
    application = create_app()

    # Create all tables in the temp database before the app starts serving.
    # Without this, any test that writes to the DB via the app would fail with
    # "no such table" errors.
    from sqlalchemy import create_engine

    from app.core.database import create_sync_url
    from app.models.base import Base

    sync_engine = create_engine(create_sync_url(application.config["TINKER_DB_PATH"]))
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    async with application.test_app():
        yield application


@pytest.fixture
async def client(app: Quart) -> Any:
    """Create a test client for the application."""
    return app.test_client()


@pytest.fixture
def mock_session() -> AsyncMock:
    """Create a mock AsyncSession for repository tests."""
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.delete = MagicMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.get = AsyncMock()
    session.close = AsyncMock()

    # Mock the scalars() return for common query patterns
    mock_scalars = MagicMock()
    mock_scalars.all = MagicMock(return_value=[])
    mock_scalars.first = MagicMock(return_value=None)
    mock_scalars.one_or_none = MagicMock(return_value=None)

    mock_result = MagicMock()
    mock_result.scalars = MagicMock(return_value=mock_scalars)
    mock_result.scalar_one_or_none = MagicMock(return_value=None)
    session.execute.return_value = mock_result

    return session
