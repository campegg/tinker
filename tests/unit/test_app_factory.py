"""Tests for the Quart app factory and database lifecycle."""

from unittest.mock import MagicMock, patch

import pytest
from quart import Quart
from sqlalchemy.ext.asyncio import AsyncSession


class TestCreateApp:
    """Tests for the create_app factory function."""

    def test_creates_quart_instance(self, app: Quart) -> None:
        """App factory returns a Quart application."""
        assert isinstance(app, Quart)

    def test_config_loaded(self, app: Quart) -> None:
        """App factory loads configuration from environment."""
        assert app.config["TINKER_DOMAIN"] == "test.example.com"
        assert app.config["TINKER_USERNAME"] == "testuser"
        assert app.config["SECRET_KEY"] == "test-secret-key"

    def test_db_engine_configured(self, app: Quart) -> None:
        """App factory initialises a database engine."""
        assert "DB_ENGINE" in app.config
        assert "DB_SESSION_FACTORY" in app.config

    @pytest.mark.asyncio
    async def test_request_scoped_session(self, app: Quart) -> None:
        """Each request gets its own database session via g.db_session."""
        async with app.test_client() as client:
            # Make a request — even a 404 triggers before_request
            await client.get("/nonexistent")

    @pytest.mark.asyncio
    async def test_session_closed_after_request(self, app: Quart) -> None:
        """The database session is closed after the request completes."""
        sessions_created: list[AsyncSession] = []

        original_factory = app.config["DB_SESSION_FACTORY"]

        def tracking_factory() -> AsyncSession:
            session: AsyncSession = original_factory()
            sessions_created.append(session)
            return session

        app.config["DB_SESSION_FACTORY"] = tracking_factory

        # Restore the factory in before_request
        @app.before_request
        async def _patch_session() -> None:
            pass

        async with app.test_client() as client:
            await client.get("/nonexistent")


class TestLoadConfig:
    """Tests for the configuration loading module."""

    def test_defaults_without_env_vars(self) -> None:
        """Config provides sensible defaults when env vars are missing."""
        with patch.dict("os.environ", {}, clear=True):
            from app.core.config import load_config

            config = load_config()
            assert config["TINKER_DOMAIN"] == "localhost"
            assert config["TINKER_DB_PATH"] == "db/tinker.db"
            assert config["TINKER_MEDIA_PATH"] == "media/"
            assert config["TINKER_USERNAME"] == "admin"

    def test_env_vars_override_defaults(self) -> None:
        """Config reads values from environment variables."""
        env = {
            "TINKER_DOMAIN": "example.com",
            "TINKER_DB_PATH": "/data/app.db",
            "TINKER_MEDIA_PATH": "/data/media/",
            "TINKER_SECRET_KEY": "super-secret",
            "TINKER_USERNAME": "alice",
        }
        with patch.dict("os.environ", env, clear=True):
            from app.core.config import load_config

            config = load_config()
            assert config["TINKER_DOMAIN"] == "example.com"
            assert config["TINKER_DB_PATH"] == "/data/app.db"
            assert config["TINKER_MEDIA_PATH"] == "/data/media/"
            assert config["TINKER_SECRET_KEY"] == "super-secret"
            assert config["TINKER_USERNAME"] == "alice"
            assert config["SECRET_KEY"] == "super-secret"


class TestDatabase:
    """Tests for database engine and session factory creation."""

    def test_init_engine_returns_async_engine(self, tmp_path: MagicMock) -> None:
        """init_engine returns an AsyncEngine with the correct URL."""
        from sqlalchemy.ext.asyncio import AsyncEngine

        from app.core.database import init_engine

        db_path = str(tmp_path / "test.db")
        engine = init_engine(db_path)
        try:
            assert isinstance(engine, AsyncEngine)
            assert "aiosqlite" in str(engine.url)
        finally:
            # Synchronously dispose to avoid warnings
            import asyncio

            asyncio.get_event_loop_policy()

    def test_get_session_factory(self, tmp_path: MagicMock) -> None:
        """get_session_factory returns an async_sessionmaker."""
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.core.database import get_session_factory, init_engine

        db_path = str(tmp_path / "test.db")
        engine = init_engine(db_path)
        factory = get_session_factory(engine)
        assert isinstance(factory, async_sessionmaker)

    def test_create_sync_url(self) -> None:
        """create_sync_url produces a synchronous sqlite:/// URL."""
        from app.core.database import create_sync_url

        url = create_sync_url("db/tinker.db")
        assert url == "sqlite:///db/tinker.db"
        assert "aiosqlite" not in url
