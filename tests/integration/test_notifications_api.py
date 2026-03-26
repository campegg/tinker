"""Integration tests for the notifications API endpoints (WP-17).

Tests:
- ``GET /admin/api/notifications``: auth guard, empty state, all notification
  types returned, ``is_following`` flag, cursor-based pagination.
- ``POST /admin/api/notifications/mark-all-read``: auth guard, CSRF guard,
  marks all rows read.
- ``POST /admin/api/follow``: auth guard, CSRF guard.
- ``POST /admin/api/unfollow``: auth guard, CSRF guard.
- ``GET /admin/notifications``: page renders as HTML.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator
from typing import Any

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
REMOTE_POST_URI = "https://remote.example.com/notes/xyz"
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


def _seed_notification(db: Any, **kwargs: Any) -> Any:
    """Helper — create and return a Notification model instance.

    Accepts any Notification field as a keyword argument to override the
    defaults.  Pass ``created_at`` explicitly when testing cursor pagination
    (SQLite's ``func.now()`` returns the same value for bulk inserts in the
    same transaction).
    """
    from app.models.notification import Notification

    defaults: dict[str, Any] = {
        "type": "like",
        "actor_uri": REMOTE_ACTOR_URI,
        "actor_name": "Alice",
        "object_uri": REMOTE_POST_URI,
        "content": None,
        "read": False,
    }
    defaults.update(kwargs)
    n = Notification(**defaults)
    db.add(n)
    return n


# ---------------------------------------------------------------------------
# GET /admin/api/notifications
# ---------------------------------------------------------------------------


class TestGetNotifications:
    """Tests for ``GET /admin/api/notifications``."""

    async def test_requires_auth(self, application: Quart) -> None:
        """Unauthenticated requests are redirected or receive 401."""
        async with application.test_client() as client:
            resp = await client.get("/admin/api/notifications")
        assert resp.status_code in (401, 302)

    async def test_empty_state(self, application: Quart) -> None:
        """Returns an empty data array when there are no notifications."""
        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/notifications")

        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert data["data"] == []
        assert data["cursor"] is None
        assert data["has_more"] is False

    async def test_returns_all_notification_types(self, application: Quart) -> None:
        """All four notification types are returned with the expected fields."""
        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            _seed_notification(db, type="like")
            _seed_notification(db, type="boost")
            _seed_notification(db, type="follow")
            _seed_notification(db, type="reply", content="<p>Hello</p>")
            await db.commit()

        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/notifications")

        assert resp.status_code == 200
        payload = json.loads(await resp.get_data())
        types = {item["type"] for item in payload["data"]}
        assert types == {"like", "boost", "follow", "reply"}

        # Verify expected fields are present on each item.
        for item in payload["data"]:
            assert "id" in item
            assert "actor_uri" in item
            assert "actor_name" in item
            assert "actor_handle" in item
            assert "actor_avatar" in item
            assert "object_uri" in item
            assert "content" in item
            assert "created_at" in item
            assert "is_following" in item

    async def test_is_following_true_when_following(self, application: Quart) -> None:
        """``is_following`` is ``True`` when an accepted Following record exists."""
        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            from app.models.following import Following

            _seed_notification(db, type="follow", actor_uri=REMOTE_ACTOR_URI)
            db.add(
                Following(
                    actor_uri=REMOTE_ACTOR_URI,
                    inbox_url="https://remote.example.com/inbox",
                    status="accepted",
                )
            )
            await db.commit()

        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/notifications")

        payload = json.loads(await resp.get_data())
        assert len(payload["data"]) == 1
        assert payload["data"][0]["is_following"] is True

    async def test_is_following_false_without_following_record(self, application: Quart) -> None:
        """``is_following`` is ``False`` when no Following record exists."""
        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            _seed_notification(db, type="follow", actor_uri=REMOTE_ACTOR_URI)
            await db.commit()

        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/notifications")

        payload = json.loads(await resp.get_data())
        assert payload["data"][0]["is_following"] is False

    async def test_cursor_pagination_first_page(self, application: Quart) -> None:
        """First page returns 50 items and ``has_more=True`` when 55 exist."""
        from datetime import UTC, datetime, timedelta

        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            base_time = datetime(2026, 1, 1, tzinfo=UTC)
            for i in range(55):
                _seed_notification(db, created_at=base_time + timedelta(seconds=i))
            await db.commit()

        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/notifications")

        payload = json.loads(await resp.get_data())
        assert len(payload["data"]) == 50
        assert payload["has_more"] is True
        assert payload["cursor"] is not None

    async def test_cursor_pagination_second_page(self, application: Quart) -> None:
        """Second page (using ``before`` cursor) returns remaining 5 items."""
        from datetime import UTC, datetime, timedelta

        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            base_time = datetime(2026, 1, 1, tzinfo=UTC)
            for i in range(55):
                _seed_notification(db, created_at=base_time + timedelta(seconds=i))
            await db.commit()

        async with application.test_client() as client:
            await _login(client)
            first = json.loads(await (await client.get("/admin/api/notifications")).get_data())
            cursor = first["cursor"]
            resp = await client.get(f"/admin/api/notifications?before={cursor}")

        payload = json.loads(await resp.get_data())
        assert len(payload["data"]) == 5
        assert payload["has_more"] is False

    async def test_invalid_before_returns_400(self, application: Quart) -> None:
        """An unparseable ``before`` parameter returns 400."""
        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/notifications?before=not-a-date")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /admin/api/notifications/mark-all-read
# ---------------------------------------------------------------------------


class TestMarkAllRead:
    """Tests for ``POST /admin/api/notifications/mark-all-read``."""

    async def test_requires_auth(self, application: Quart) -> None:
        """Unauthenticated requests are redirected or receive 401."""
        async with application.test_client() as client:
            resp = await client.post(
                "/admin/api/notifications/mark-all-read",
                headers={"Content-Type": _JSON},
                data=json.dumps({}),
            )
        assert resp.status_code in (401, 302)

    async def test_requires_csrf(self, application: Quart) -> None:
        """Requests without a valid CSRF token receive 403."""
        async with application.test_client() as client:
            await _login(client)
            resp = await client.post(
                "/admin/api/notifications/mark-all-read",
                headers={"Content-Type": _JSON, "X-CSRF-Token": "bad"},
                data=json.dumps({}),
            )
        assert resp.status_code == 403

    async def test_marks_all_notifications_read(self, application: Quart) -> None:
        """All unread notifications are set to ``read=True``."""
        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            _seed_notification(db, read=False)
            _seed_notification(db, read=False)
            await db.commit()

        async with application.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/notifications/mark-all-read",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({}),
            )

        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert data == {"status": "ok"}

        # Verify rows in DB.
        from sqlalchemy import select

        async with session_factory() as db:
            from app.models.notification import Notification

            result = await db.execute(
                select(Notification).where(Notification.read == False)  # noqa: E712
            )
            assert result.scalars().all() == []


# ---------------------------------------------------------------------------
# POST /admin/api/follow
# ---------------------------------------------------------------------------


class TestFollowEndpoint:
    """Auth and CSRF guard tests for ``POST /admin/api/follow``."""

    async def test_requires_auth(self, application: Quart) -> None:
        """Unauthenticated requests are redirected or receive 401."""
        async with application.test_client() as client:
            resp = await client.post(
                "/admin/api/follow",
                headers={"Content-Type": _JSON},
                data=json.dumps({"actor_uri": REMOTE_ACTOR_URI}),
            )
        assert resp.status_code in (401, 302)

    async def test_requires_csrf(self, application: Quart) -> None:
        """Requests without a valid CSRF token receive 403."""
        async with application.test_client() as client:
            await _login(client)
            resp = await client.post(
                "/admin/api/follow",
                headers={"Content-Type": _JSON, "X-CSRF-Token": "bad"},
                data=json.dumps({"actor_uri": REMOTE_ACTOR_URI}),
            )
        assert resp.status_code == 403

    async def test_missing_actor_uri_returns_400(self, application: Quart) -> None:
        """Missing ``actor_uri`` field returns 400."""
        async with application.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/follow",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({}),
            )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /admin/api/unfollow
# ---------------------------------------------------------------------------


class TestUnfollowEndpoint:
    """Auth and CSRF guard tests for ``POST /admin/api/unfollow``."""

    async def test_requires_auth(self, application: Quart) -> None:
        """Unauthenticated requests are redirected or receive 401."""
        async with application.test_client() as client:
            resp = await client.post(
                "/admin/api/unfollow",
                headers={"Content-Type": _JSON},
                data=json.dumps({"actor_uri": REMOTE_ACTOR_URI}),
            )
        assert resp.status_code in (401, 302)

    async def test_requires_csrf(self, application: Quart) -> None:
        """Requests without a valid CSRF token receive 403."""
        async with application.test_client() as client:
            await _login(client)
            resp = await client.post(
                "/admin/api/unfollow",
                headers={"Content-Type": _JSON, "X-CSRF-Token": "bad"},
                data=json.dumps({"actor_uri": REMOTE_ACTOR_URI}),
            )
        assert resp.status_code == 403

    async def test_missing_actor_uri_returns_400(self, application: Quart) -> None:
        """Missing ``actor_uri`` field returns 400."""
        async with application.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/unfollow",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({}),
            )
        assert resp.status_code == 400

    async def test_unfollow_noop_when_not_following(self, application: Quart) -> None:
        """Unfollow with no existing record returns ``{"status": "ok"}``."""
        async with application.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/unfollow",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({"actor_uri": REMOTE_ACTOR_URI}),
            )
        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert data == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /admin/notifications (HTML shell)
# ---------------------------------------------------------------------------


class TestNotificationsPage:
    """Tests for the ``GET /admin/notifications`` HTML shell route."""

    async def test_requires_auth(self, application: Quart) -> None:
        """Unauthenticated requests are redirected or receive 401."""
        async with application.test_client() as client:
            resp = await client.get("/admin/notifications")
        assert resp.status_code in (401, 302)

    async def test_returns_html(self, application: Quart) -> None:
        """Authenticated request returns the notifications HTML shell."""
        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/notifications")

        assert resp.status_code == 200
        assert "text/html" in resp.content_type
        body = await resp.get_data(as_text=True)
        assert "notification-list" in body
        assert "nav-bar" in body
