"""Integration tests for the profile API endpoints (WP-18).

Tests:
- ``GET /admin/api/profile``: auth guard, default values, seeded values.
- ``PATCH /admin/api/profile``: auth guard, CSRF guard, display_name update,
  bio Markdown rendering.
- ``GET /admin/profile``: page renders as HTML.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from quart import Quart

import app.admin.auth as auth_module
from app import create_app
from app.admin.auth import hash_password

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOCAL_DOMAIN = "test.example.com"
LOCAL_USERNAME = "testuser"
_JSON = "application/json"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def application(tmp_path: Any) -> AsyncGenerator[Quart, None]:
    """Test application with schema and seeded admin password."""
    os.environ["TINKER_DOMAIN"] = LOCAL_DOMAIN
    os.environ["TINKER_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["TINKER_MEDIA_PATH"] = str(tmp_path / "media")
    os.environ["TINKER_SECRET_KEY"] = "test-secret-key"
    os.environ["TINKER_USERNAME"] = LOCAL_USERNAME

    quart_app = create_app()

    from sqlalchemy import create_engine

    from app.models.base import Base

    sync_engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    auth_module._login_attempts.clear()

    async with quart_app.test_app():
        session_factory = quart_app.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            from app.services.settings import SettingsService

            svc = SettingsService(db)
            await svc.set_admin_password_hash(hash_password("pass"))

        yield quart_app

    auth_module._login_attempts.clear()


async def _login(client: Any) -> dict[str, str]:
    """Log in and return ``{"X-CSRF-Token": "..."}`` header dict."""
    resp = await client.get("/login")
    body = await resp.get_data(as_text=True)
    marker = 'name="csrf_token" value="'
    start = body.index(marker) + len(marker)
    end = body.index('"', start)
    login_csrf = body[start:end]

    await client.post(
        "/login",
        form={
            "username": LOCAL_USERNAME,
            "password": "pass",
            "csrf_token": login_csrf,
        },
    )

    async with client.session_transaction() as sess:
        csrf = sess.get("csrf_token", "")
    return {"X-CSRF-Token": csrf}


# ---------------------------------------------------------------------------
# GET /admin/api/profile
# ---------------------------------------------------------------------------


class TestGetProfile:
    """Tests for ``GET /admin/api/profile``."""

    async def test_requires_auth(self, application: Quart) -> None:
        """Unauthenticated requests are redirected or receive 401."""
        async with application.test_client() as client:
            resp = await client.get("/admin/api/profile")
        assert resp.status_code in (401, 302)

    async def test_returns_defaults(self, application: Quart) -> None:
        """Returns empty strings and empty list when no profile is set."""
        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/profile")

        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert "display_name" in data
        assert "bio" in data
        assert "bio_html" in data
        assert "avatar_url" in data
        assert "header_image_url" in data
        assert isinstance(data["links"], list)
        assert "handle" in data

    async def test_returns_seeded_values(self, application: Quart) -> None:
        """Returns settings-table values when they have been set."""
        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            from app.services.settings import SettingsService

            svc = SettingsService(db)
            await svc.set_display_name("Alice")
            await svc.set_bio("Hello **world**")
            await svc.set_links(["https://example.com"])

        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/profile")

        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert data["display_name"] == "Alice"
        assert data["bio"] == "Hello **world**"
        assert "https://example.com" in data["links"]


# ---------------------------------------------------------------------------
# PATCH /admin/api/profile
# ---------------------------------------------------------------------------


class TestPatchProfile:
    """Tests for ``PATCH /admin/api/profile``."""

    async def test_requires_auth(self, application: Quart) -> None:
        """Unauthenticated requests are redirected or receive 401."""
        async with application.test_client() as client:
            resp = await client.patch(
                "/admin/api/profile",
                headers={"Content-Type": _JSON},
                data=json.dumps({"display_name": "Test"}),
            )
        assert resp.status_code in (401, 302)

    async def test_requires_csrf(self, application: Quart) -> None:
        """Requests without a valid CSRF token receive 403."""
        async with application.test_client() as client:
            await _login(client)
            resp = await client.patch(
                "/admin/api/profile",
                headers={"Content-Type": _JSON, "X-CSRF-Token": "bad"},
                data=json.dumps({"display_name": "Test"}),
            )
        assert resp.status_code == 403

    async def test_updates_display_name(self, application: Quart) -> None:
        """PATCH with ``display_name`` persists the new value."""
        # Patch fan-out so delivery doesn't hit the network.
        with (
            patch("app.admin.api.DeliveryService") as mock_dsvc,
            patch("app.admin.api.dispatch_new_items"),
        ):
            mock_dsvc.return_value.fan_out = AsyncMock(return_value=[])

            async with application.test_client() as client:
                headers = await _login(client)
                resp = await client.patch(
                    "/admin/api/profile",
                    headers={**headers, "Content-Type": _JSON},
                    data=json.dumps({"display_name": "New Name"}),
                )

        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert data == {"status": "ok"}

        # Verify the settings table was updated.
        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            from app.services.settings import SettingsService

            svc = SettingsService(db)
            assert await svc.get_display_name() == "New Name"

    async def test_renders_bio_markdown(self, application: Quart) -> None:
        """PATCH with Markdown bio stores raw source; GET returns rendered HTML."""
        with (
            patch("app.admin.api.DeliveryService") as mock_dsvc,
            patch("app.admin.api.dispatch_new_items"),
        ):
            mock_dsvc.return_value.fan_out = AsyncMock(return_value=[])

            async with application.test_client() as client:
                headers = await _login(client)
                await client.patch(
                    "/admin/api/profile",
                    headers={**headers, "Content-Type": _JSON},
                    data=json.dumps({"bio": "Hello **world**"}),
                )
                resp = await client.get("/admin/api/profile")

        data = json.loads(await resp.get_data())
        assert data["bio"] == "Hello **world**"
        assert "<strong>" in data["bio_html"] or "<b>" in data["bio_html"]

    async def test_invalid_display_name_type_returns_400(self, application: Quart) -> None:
        """A non-string ``display_name`` returns 400."""
        async with application.test_client() as client:
            headers = await _login(client)
            resp = await client.patch(
                "/admin/api/profile",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({"display_name": 42}),
            )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /admin/profile (HTML shell)
# ---------------------------------------------------------------------------


class TestProfilePage:
    """Tests for the ``GET /admin/profile`` HTML shell route."""

    async def test_requires_auth(self, application: Quart) -> None:
        """Unauthenticated requests are redirected or receive 401."""
        async with application.test_client() as client:
            resp = await client.get("/admin/profile")
        assert resp.status_code in (401, 302)

    async def test_returns_html(self, application: Quart) -> None:
        """Authenticated request returns the profile HTML shell."""
        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/profile")

        assert resp.status_code == 200
        assert "text/html" in resp.content_type
        body = await resp.get_data(as_text=True)
        assert "profile-view" in body
        assert "nav-bar" in body
