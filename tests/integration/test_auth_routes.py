"""Integration tests for admin authentication routes.

Tests the login/logout flow, session creation, CSRF validation, and rate
limiting against a real Quart test client backed by a real SQLite database.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any, cast

import pytest
from quart import Quart

from app import create_app
from app.admin.auth import hash_password


@pytest.fixture(autouse=True)
def _reset_rate_limit() -> None:
    """Clear in-memory rate limit state before each test."""
    import app.admin.auth as auth_module

    auth_module._login_attempts.clear()


@pytest.fixture
async def auth_app(tmp_path: Any) -> AsyncGenerator[Quart, None]:
    """Create a test application with a known admin password pre-seeded."""
    os.environ["TINKER_DOMAIN"] = "test.example.com"
    os.environ["TINKER_DB_PATH"] = str(tmp_path / "auth_test.db")
    os.environ["TINKER_MEDIA_PATH"] = str(tmp_path / "media")
    os.environ["TINKER_SECRET_KEY"] = "test-secret-for-auth"
    os.environ["TINKER_USERNAME"] = "admin"
    os.environ["TINKER_ADMIN_PASSWORD"] = ""  # We seed manually below.

    application = create_app()

    # Create the database schema in the temp database.
    from sqlalchemy import create_engine

    from app.models.base import Base

    sync_engine = create_engine(f"sqlite:///{tmp_path / 'auth_test.db'}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    async with application.test_app():
        # Seed the admin password hash directly via the settings service so
        # tests are independent of the env-var seeding path.
        from sqlalchemy.ext.asyncio import AsyncSession

        session_factory = application.config["DB_SESSION_FACTORY"]
        db: AsyncSession = session_factory()
        try:
            from app.services.settings import SettingsService

            svc = SettingsService(db)
            await svc.set_admin_password_hash(hash_password("correct-password"))
        finally:
            await db.close()

        yield application


@pytest.fixture
async def auth_client(auth_app: Quart) -> AsyncGenerator[Any, None]:
    """Return an entered test client for the auth app.

    Enters the client context here so individual tests do not double-wrap
    it with ``async with``, which would push a second app context and cause
    SQLAlchemy connection pool warnings on teardown.
    """
    async with auth_app.test_client() as client:
        yield client


# ---------------------------------------------------------------------------
# GET /login
# ---------------------------------------------------------------------------


class TestLoginPage:
    """Tests for GET /login."""

    async def test_returns_200(self, auth_client: Any) -> None:
        """GET /login returns 200 OK."""
        resp = await auth_client.get("/login")
        assert resp.status_code == 200

    async def test_returns_html(self, auth_client: Any) -> None:
        """GET /login returns HTML content."""
        resp = await auth_client.get("/login")
        assert "text/html" in resp.content_type

    async def test_contains_form(self, auth_client: Any) -> None:
        """GET /login response contains the login form."""
        resp = await auth_client.get("/login")
        body = await resp.get_data(as_text=True)
        assert "<form" in body
        assert 'name="username"' in body
        assert 'name="password"' in body

    async def test_contains_csrf_token(self, auth_client: Any) -> None:
        """GET /login embeds a CSRF token in the form."""
        resp = await auth_client.get("/login")
        body = await resp.get_data(as_text=True)
        assert 'name="csrf_token"' in body
        # Placeholder should have been replaced with an actual token value.
        assert "{{csrf_token}}" not in body

    async def test_redirects_if_already_authenticated(self, auth_client: Any) -> None:
        """GET /login redirects authenticated users to the admin area."""
        async with auth_client.session_transaction() as s:
            s["authenticated"] = True
        resp = await auth_client.get("/login")
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/admin/"


# ---------------------------------------------------------------------------
# POST /login — success
# ---------------------------------------------------------------------------


class TestLoginSuccess:
    """Tests for successful POST /login."""

    async def _get_csrf_token(self, client: Any) -> str:
        """Fetch the login page and extract the CSRF token from the form."""
        resp = await client.get("/login")
        body = await resp.get_data(as_text=True)
        # Extract token value from: <input type="hidden" name="csrf_token" value="...">
        marker = 'name="csrf_token" value="'
        start = body.index(marker) + len(marker)
        end = body.index('"', start)
        return cast(str, body[start:end])

    async def test_redirects_to_admin_on_success(self, auth_client: Any) -> None:
        """Correct credentials redirect to /admin/."""
        csrf = await self._get_csrf_token(auth_client)
        resp = await auth_client.post(
            "/login",
            form={
                "username": "admin",
                "password": "correct-password",
                "csrf_token": csrf,
            },
        )
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/admin/"

    async def test_session_is_authenticated_after_login(self, auth_client: Any) -> None:
        """After login the session contains authenticated=True."""
        csrf = await self._get_csrf_token(auth_client)
        await auth_client.post(
            "/login",
            form={
                "username": "admin",
                "password": "correct-password",
                "csrf_token": csrf,
            },
        )
        async with auth_client.session_transaction() as s:
            assert s.get("authenticated") is True
            assert s.get("username") == "admin"


# ---------------------------------------------------------------------------
# POST /login — failure cases
# ---------------------------------------------------------------------------


class TestLoginFailure:
    """Tests for failed POST /login."""

    async def _get_csrf_token(self, client: Any) -> str:
        """Fetch a fresh CSRF token from the login page."""
        resp = await client.get("/login")
        body = await resp.get_data(as_text=True)
        marker = 'name="csrf_token" value="'
        start = body.index(marker) + len(marker)
        end = body.index('"', start)
        return cast(str, body[start:end])

    async def test_wrong_password_returns_401(self, auth_client: Any) -> None:
        """Wrong password returns 401 with an error message."""
        csrf = await self._get_csrf_token(auth_client)
        resp = await auth_client.post(
            "/login",
            form={
                "username": "admin",
                "password": "wrong-password",
                "csrf_token": csrf,
            },
        )
        assert resp.status_code == 401
        body = await resp.get_data(as_text=True)
        assert "Invalid username or password" in body

    async def test_wrong_username_returns_401(self, auth_client: Any) -> None:
        """Wrong username returns 401 with an error message."""
        csrf = await self._get_csrf_token(auth_client)
        resp = await auth_client.post(
            "/login",
            form={
                "username": "notadmin",
                "password": "correct-password",
                "csrf_token": csrf,
            },
        )
        assert resp.status_code == 401

    async def test_missing_csrf_returns_400(self, auth_client: Any) -> None:
        """Missing CSRF token returns 400."""
        resp = await auth_client.post(
            "/login",
            form={
                "username": "admin",
                "password": "correct-password",
            },
        )
        assert resp.status_code == 400

    async def test_invalid_csrf_returns_400(self, auth_client: Any) -> None:
        """Invalid CSRF token returns 400."""
        await auth_client.get("/login")
        resp = await auth_client.post(
            "/login",
            form={
                "username": "admin",
                "password": "correct-password",
                "csrf_token": "invalid-token",
            },
        )
        assert resp.status_code == 400

    async def test_no_session_on_failed_login(self, auth_client: Any) -> None:
        """Failed login does not create an authenticated session."""
        csrf = await self._get_csrf_token(auth_client)
        await auth_client.post(
            "/login",
            form={
                "username": "admin",
                "password": "wrong-password",
                "csrf_token": csrf,
            },
        )
        async with auth_client.session_transaction() as s:
            assert not s.get("authenticated")


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    """Tests for login endpoint rate limiting."""

    async def _get_csrf_token(self, client: Any) -> str:
        """Fetch a fresh CSRF token from the login page."""
        resp = await client.get("/login")
        body = await resp.get_data(as_text=True)
        marker = 'name="csrf_token" value="'
        start = body.index(marker) + len(marker)
        end = body.index('"', start)
        return cast(str, body[start:end])

    async def test_rate_limit_after_max_attempts(self, auth_client: Any) -> None:
        """After 5 failed attempts the endpoint returns 429."""
        for _ in range(5):
            csrf = await self._get_csrf_token(auth_client)
            await auth_client.post(
                "/login",
                form={
                    "username": "admin",
                    "password": "wrong",
                    "csrf_token": csrf,
                },
            )

        # 6th attempt should be rate limited.
        csrf = await self._get_csrf_token(auth_client)
        resp = await auth_client.post(
            "/login",
            form={
                "username": "admin",
                "password": "wrong",
                "csrf_token": csrf,
            },
        )
        assert resp.status_code == 429


# ---------------------------------------------------------------------------
# POST /logout
# ---------------------------------------------------------------------------


class TestLogout:
    """Tests for POST /logout."""

    async def _login(self, client: Any) -> None:
        """Perform a full login flow to establish an authenticated session.

        Args:
            client: The test client to perform the login with.
        """
        resp = await client.get("/login")
        body = await resp.get_data(as_text=True)
        marker = 'name="csrf_token" value="'
        start = body.index(marker) + len(marker)
        end = body.index('"', start)
        csrf = body[start:end]
        await client.post(
            "/login",
            form={
                "username": "admin",
                "password": "correct-password",
                "csrf_token": csrf,
            },
        )

    async def test_logout_clears_session(self, auth_client: Any) -> None:
        """POST /logout clears the authenticated session."""
        await self._login(auth_client)
        async with auth_client.session_transaction() as s:
            csrf = s.get("csrf_token", "")
        await auth_client.post("/logout", form={"csrf_token": csrf})
        async with auth_client.session_transaction() as s:
            assert not s.get("authenticated")

    async def test_logout_redirects_to_login(self, auth_client: Any) -> None:
        """POST /logout redirects to /login."""
        await self._login(auth_client)
        async with auth_client.session_transaction() as s:
            csrf = s.get("csrf_token", "")
        resp = await auth_client.post("/logout", form={"csrf_token": csrf})
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/login"

    async def test_logout_without_csrf_returns_400(self, auth_client: Any) -> None:
        """POST /logout without a CSRF token returns 400."""
        await self._login(auth_client)
        resp = await auth_client.post("/logout", form={})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


class TestAuthGuard:
    """Tests for the require_auth decorator on admin routes."""

    async def test_unauthenticated_admin_redirects_to_login(self, auth_client: Any) -> None:
        """Unauthenticated GET /admin/ redirects to /login."""
        resp = await auth_client.get("/admin/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    async def test_authenticated_admin_is_accessible(self, auth_client: Any) -> None:
        """Authenticated GET /admin/ does not redirect to /login."""
        async with auth_client.session_transaction() as s:
            s["authenticated"] = True
        resp = await auth_client.get("/admin/")
        # Should redirect to /admin/timeline (or another admin page), not /login.
        if resp.status_code == 302:
            assert "/login" not in resp.headers.get("Location", "")
