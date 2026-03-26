"""Integration tests for the actor search and lookup API endpoints (WP-18).

Tests:
- ``GET /admin/api/search``: auth guard, missing ``q``, invalid handle,
  WebFinger returns 404, successful lookup.
- ``GET /admin/api/actor``: auth guard, missing ``uri``, cached actor returned,
  ``is_following`` flag.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
REMOTE_ACTOR_URI = "https://remote.example.com/users/alice"
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
# GET /admin/api/search
# ---------------------------------------------------------------------------


class TestSearchActor:
    """Tests for ``GET /admin/api/search``."""

    async def test_requires_auth(self, application: Quart) -> None:
        """Unauthenticated requests are redirected or receive 401."""
        async with application.test_client() as client:
            resp = await client.get("/admin/api/search?q=@alice@remote.example.com")
        assert resp.status_code in (401, 302)

    async def test_missing_q_returns_400(self, application: Quart) -> None:
        """Missing ``q`` parameter returns 400."""
        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/search")
        assert resp.status_code == 400

    async def test_invalid_handle_returns_400(self, application: Quart) -> None:
        """A handle with no domain part returns 400."""
        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/search?q=notahandle")
        assert resp.status_code == 400

    async def test_webfinger_not_found_returns_404(self, application: Quart) -> None:
        """When WebFinger returns an HTTP error, the endpoint returns 404."""
        import httpx

        with patch("app.admin.api.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "404", request=MagicMock(), response=MagicMock()
            )
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_http

            async with application.test_client() as client:
                await _login(client)
                resp = await client.get("/admin/api/search?q=@alice@remote.example.com")

        assert resp.status_code == 404

    async def test_webfinger_missing_self_link_returns_404(self, application: Quart) -> None:
        """When WebFinger has no ``self`` link, the endpoint returns 404."""
        with patch("app.admin.api.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"links": []}  # No self link
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_http

            async with application.test_client() as client:
                await _login(client)
                resp = await client.get("/admin/api/search?q=@alice@remote.example.com")

        assert resp.status_code == 404

    async def test_successful_search_returns_actor_data(self, application: Quart) -> None:
        """Successful WebFinger + actor fetch returns actor fields."""
        webfinger_resp = {
            "links": [
                {
                    "rel": "self",
                    "type": "application/activity+json",
                    "href": REMOTE_ACTOR_URI,
                }
            ]
        }

        with patch("app.admin.api.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = webfinger_resp
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_http

            # Mock RemoteActorService so we don't hit the network.
            mock_actor = MagicMock()
            mock_actor.uri = REMOTE_ACTOR_URI
            mock_actor.display_name = "Alice"
            mock_actor.handle = "@alice@remote.example.com"
            mock_actor.avatar_url = ""
            mock_actor.header_image_url = ""
            mock_actor.bio = ""
            with patch("app.admin.api.RemoteActorService") as mock_svc_cls:
                mock_svc = AsyncMock()
                mock_svc.get_by_uri = AsyncMock(return_value=mock_actor)
                mock_svc_cls.return_value = mock_svc

                async with application.test_client() as client:
                    await _login(client)
                    resp = await client.get("/admin/api/search?q=@alice@remote.example.com")

        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert data["uri"] == REMOTE_ACTOR_URI
        assert data["display_name"] == "Alice"
        assert "handle" in data
        assert "is_following" in data


# ---------------------------------------------------------------------------
# GET /admin/api/actor
# ---------------------------------------------------------------------------


class TestGetActor:
    """Tests for ``GET /admin/api/actor``."""

    async def test_requires_auth(self, application: Quart) -> None:
        """Unauthenticated requests are redirected or receive 401."""
        async with application.test_client() as client:
            resp = await client.get(f"/admin/api/actor?uri={REMOTE_ACTOR_URI}")
        assert resp.status_code in (401, 302)

    async def test_missing_uri_returns_400(self, application: Quart) -> None:
        """Missing ``uri`` parameter returns 400."""
        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/actor")
        assert resp.status_code == 400

    async def test_returns_cached_actor(self, application: Quart) -> None:
        """Returns stored actor data when the actor is cached in RemoteActor."""
        # Mock RemoteActorService to return an actor without hitting the network.
        mock_actor = MagicMock()
        mock_actor.uri = REMOTE_ACTOR_URI
        mock_actor.display_name = "Alice"
        mock_actor.handle = "@alice@remote.example.com"
        mock_actor.avatar_url = ""
        mock_actor.header_image_url = ""
        mock_actor.bio = ""

        with patch("app.admin.api.RemoteActorService") as mock_svc_cls:
            mock_svc = AsyncMock()
            mock_svc.get_by_uri = AsyncMock(return_value=mock_actor)
            mock_svc_cls.return_value = mock_svc

            async with application.test_client() as client:
                await _login(client)
                resp = await client.get(f"/admin/api/actor?uri={REMOTE_ACTOR_URI}")

        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert data["uri"] == REMOTE_ACTOR_URI
        assert data["display_name"] == "Alice"
        assert "handle" in data
        assert "avatar_url" in data
        assert "bio" in data
        assert "is_following" in data

    async def test_is_following_true_when_following(self, application: Quart) -> None:
        """``is_following`` is ``True`` when an accepted Following record exists."""
        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            from app.models.following import Following

            db.add(
                Following(
                    actor_uri=REMOTE_ACTOR_URI,
                    inbox_url="https://remote.example.com/inbox",
                    status="accepted",
                )
            )
            await db.commit()

        mock_actor = MagicMock()
        mock_actor.uri = REMOTE_ACTOR_URI
        mock_actor.display_name = "Alice"
        mock_actor.handle = "@alice@remote.example.com"
        mock_actor.avatar_url = ""
        mock_actor.header_image_url = ""
        mock_actor.bio = ""

        with patch("app.admin.api.RemoteActorService") as mock_svc_cls:
            mock_svc = AsyncMock()
            mock_svc.get_by_uri = AsyncMock(return_value=mock_actor)
            mock_svc_cls.return_value = mock_svc

            async with application.test_client() as client:
                await _login(client)
                resp = await client.get(f"/admin/api/actor?uri={REMOTE_ACTOR_URI}")

        data = json.loads(await resp.get_data())
        assert data["is_following"] is True

    async def test_actor_not_found_returns_404(self, application: Quart) -> None:
        """Returns 404 when ``RemoteActorService`` returns ``None``."""
        with patch("app.admin.api.RemoteActorService") as mock_svc_cls:
            mock_svc = AsyncMock()
            mock_svc.get_by_uri = AsyncMock(return_value=None)
            mock_svc_cls.return_value = mock_svc

            async with application.test_client() as client:
                await _login(client)
                resp = await client.get(f"/admin/api/actor?uri={REMOTE_ACTOR_URI}")

        assert resp.status_code == 404
