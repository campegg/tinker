"""Integration tests for the social graph API endpoints (WP-18).

Tests:
- ``GET /admin/api/following``: auth guard, empty state, records returned,
  pagination.
- ``GET /admin/api/followers``: auth guard, empty state, records returned.
- ``DELETE /admin/api/followers``: auth guard, CSRF guard, deletes record,
  404 when not found.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
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
REMOTE_INBOX_URL = "https://remote.example.com/inbox"
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


def _seed_following(db: Any, actor_uri: str = REMOTE_ACTOR_URI, **kwargs: Any) -> Any:
    """Create and return a Following model instance."""
    from app.models.following import Following

    defaults: dict[str, Any] = {
        "actor_uri": actor_uri,
        "inbox_url": REMOTE_INBOX_URL,
        "status": "accepted",
    }
    defaults.update(kwargs)
    row = Following(**defaults)
    db.add(row)
    return row


def _seed_follower(db: Any, actor_uri: str = REMOTE_ACTOR_URI, **kwargs: Any) -> Any:
    """Create and return a Follower model instance."""
    from app.models.follower import Follower

    defaults: dict[str, Any] = {
        "actor_uri": actor_uri,
        "inbox_url": REMOTE_INBOX_URL,
        "status": "accepted",
    }
    defaults.update(kwargs)
    row = Follower(**defaults)
    db.add(row)
    return row


# ---------------------------------------------------------------------------
# GET /admin/api/following
# ---------------------------------------------------------------------------


class TestListFollowing:
    """Tests for ``GET /admin/api/following``."""

    async def test_requires_auth(self, application: Quart) -> None:
        """Unauthenticated requests are redirected or receive 401."""
        async with application.test_client() as client:
            resp = await client.get("/admin/api/following")
        assert resp.status_code in (401, 302)

    async def test_empty_state(self, application: Quart) -> None:
        """Returns an empty data array when there are no following records."""
        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/following")

        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert data["data"] == []
        assert data["cursor"] is None
        assert data["has_more"] is False

    async def test_returns_accepted_records(self, application: Quart) -> None:
        """Returns accepted following records with expected fields."""
        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            _seed_following(db, display_name="Alice")
            await db.commit()

        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/following")

        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert len(data["data"]) == 1
        item = data["data"][0]
        assert item["actor_uri"] == REMOTE_ACTOR_URI
        assert "display_name" in item
        assert "handle" in item
        assert "avatar_url" in item

    async def test_pagination(self, application: Quart) -> None:
        """First page returns 20 items and ``has_more=True`` when 25 exist."""
        base_time = datetime(2026, 1, 1, tzinfo=UTC)
        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            for i in range(25):
                _seed_following(
                    db,
                    actor_uri=f"https://remote.example.com/users/user{i}",
                    created_at=base_time + timedelta(seconds=i),
                )
            await db.commit()

        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/following")

        payload = json.loads(await resp.get_data())
        assert len(payload["data"]) == 20
        assert payload["has_more"] is True
        assert payload["cursor"] is not None

    async def test_invalid_before_returns_400(self, application: Quart) -> None:
        """An unparseable ``before`` parameter returns 400."""
        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/following?before=not-a-date")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /admin/api/followers
# ---------------------------------------------------------------------------


class TestListFollowers:
    """Tests for ``GET /admin/api/followers``."""

    async def test_requires_auth(self, application: Quart) -> None:
        """Unauthenticated requests are redirected or receive 401."""
        async with application.test_client() as client:
            resp = await client.get("/admin/api/followers")
        assert resp.status_code in (401, 302)

    async def test_empty_state(self, application: Quart) -> None:
        """Returns an empty data array when there are no follower records."""
        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/followers")

        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert data["data"] == []
        assert data["cursor"] is None
        assert data["has_more"] is False

    async def test_returns_accepted_records(self, application: Quart) -> None:
        """Returns accepted follower records with expected fields."""
        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            _seed_follower(db, display_name="Bob")
            await db.commit()

        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/followers")

        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert len(data["data"]) == 1
        item = data["data"][0]
        assert item["actor_uri"] == REMOTE_ACTOR_URI
        assert "display_name" in item
        assert "handle" in item

    async def test_pagination(self, application: Quart) -> None:
        """First page returns 20 items and ``has_more=True`` when 25 exist."""
        base_time = datetime(2026, 1, 1, tzinfo=UTC)
        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            for i in range(25):
                _seed_follower(
                    db,
                    actor_uri=f"https://remote.example.com/users/user{i}",
                    created_at=base_time + timedelta(seconds=i),
                )
            await db.commit()

        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/followers")

        payload = json.loads(await resp.get_data())
        assert len(payload["data"]) == 20
        assert payload["has_more"] is True


# ---------------------------------------------------------------------------
# DELETE /admin/api/followers
# ---------------------------------------------------------------------------


class TestRemoveFollower:
    """Tests for ``DELETE /admin/api/followers``."""

    async def test_requires_auth(self, application: Quart) -> None:
        """Unauthenticated requests are redirected or receive 401."""
        async with application.test_client() as client:
            resp = await client.delete(
                "/admin/api/followers",
                headers={"Content-Type": _JSON},
                data=json.dumps({"actor_uri": REMOTE_ACTOR_URI}),
            )
        assert resp.status_code in (401, 302)

    async def test_requires_csrf(self, application: Quart) -> None:
        """Requests without a valid CSRF token receive 403."""
        async with application.test_client() as client:
            await _login(client)
            resp = await client.delete(
                "/admin/api/followers",
                headers={"Content-Type": _JSON, "X-CSRF-Token": "bad"},
                data=json.dumps({"actor_uri": REMOTE_ACTOR_URI}),
            )
        assert resp.status_code == 403

    async def test_deletes_follower_record(self, application: Quart) -> None:
        """Deletes the Follower row and returns ``{"status": "ok"}``."""
        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            _seed_follower(db)
            await db.commit()

        async with application.test_client() as client:
            headers = await _login(client)
            resp = await client.delete(
                "/admin/api/followers",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({"actor_uri": REMOTE_ACTOR_URI}),
            )

        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert data == {"status": "ok"}

        # Verify the row is gone.
        from sqlalchemy import select

        async with session_factory() as db:
            from app.models.follower import Follower

            result = await db.execute(
                select(Follower).where(Follower.actor_uri == REMOTE_ACTOR_URI)
            )
            assert result.scalars().first() is None

    async def test_returns_404_when_not_found(self, application: Quart) -> None:
        """Returns 404 when the actor_uri doesn't match any follower."""
        async with application.test_client() as client:
            headers = await _login(client)
            resp = await client.delete(
                "/admin/api/followers",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({"actor_uri": REMOTE_ACTOR_URI}),
            )
        assert resp.status_code == 404

    async def test_deletes_without_reject_when_no_activity_uri(self, application: Quart) -> None:
        """Follower with no ``follow_activity_uri`` is deleted without Reject delivery."""
        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            _seed_follower(db, follow_activity_uri=None)
            await db.commit()

        with patch("app.admin.api.dispatch_new_items") as mock_dispatch:
            async with application.test_client() as client:
                headers = await _login(client)
                await client.delete(
                    "/admin/api/followers",
                    headers={**headers, "Content-Type": _JSON},
                    data=json.dumps({"actor_uri": REMOTE_ACTOR_URI}),
                )

        # dispatch_new_items should not have been called (no Reject activity).
        mock_dispatch.assert_not_called()

    async def test_sends_reject_when_activity_uri_set(self, application: Quart) -> None:
        """Follower with ``follow_activity_uri`` triggers Reject delivery."""
        follow_activity_uri = "https://remote.example.com/activities/follow/1"
        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            _seed_follower(db, follow_activity_uri=follow_activity_uri)
            await db.commit()

        with (
            patch("app.admin.api.DeliveryService") as mock_dsvc,
            patch("app.admin.api.dispatch_new_items") as mock_dispatch,
        ):
            mock_item = MagicMock()
            mock_dsvc.return_value.deliver_to_inbox = AsyncMock(return_value=mock_item)

            async with application.test_client() as client:
                headers = await _login(client)
                resp = await client.delete(
                    "/admin/api/followers",
                    headers={**headers, "Content-Type": _JSON},
                    data=json.dumps({"actor_uri": REMOTE_ACTOR_URI}),
                )

        assert resp.status_code == 200
        mock_dispatch.assert_called_once()
