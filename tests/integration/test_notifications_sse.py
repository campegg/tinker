"""Integration tests for the SSE notifications endpoints (WP-16).

Tests:
- ``GET /admin/api/notifications/unread-count``: auth guard, zero count,
  non-zero count after seeding a notification.
- ``GET /admin/api/notifications/events``: auth guard, streams a notification
  event pushed onto the ``NOTIFICATION_QUEUE``.
"""

from __future__ import annotations

import asyncio
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


async def _login(client: Any) -> None:
    """Log in with the test credentials."""
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


# ---------------------------------------------------------------------------
# Unread count
# ---------------------------------------------------------------------------


class TestUnreadCount:
    """Tests for ``GET /admin/api/notifications/unread-count``."""

    async def test_requires_auth(self, application: Quart) -> None:
        """Unauthenticated requests are redirected or receive 401."""
        async with application.test_client() as client:
            resp = await client.get("/admin/api/notifications/unread-count")
        assert resp.status_code in (401, 302)

    async def test_returns_zero_when_no_notifications(self, application: Quart) -> None:
        """Returns ``{"count": 0}`` when there are no notifications."""
        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/notifications/unread-count")

        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert data == {"count": 0}

    async def test_returns_correct_count(self, application: Quart) -> None:
        """Returns the actual unread count after seeding a notification."""
        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            from app.models.notification import Notification

            db.add(Notification(type="like", actor_uri="https://example.com/users/bob"))
            await db.commit()

        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/notifications/unread-count")

        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert data == {"count": 1}


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------


class TestNotificationEvents:
    """Tests for ``GET /admin/api/notifications/events``."""

    async def test_requires_auth(self, application: Quart) -> None:
        """Unauthenticated requests are redirected or receive 401."""
        async with application.test_client() as client:
            resp = await client.get("/admin/api/notifications/events")
        assert resp.status_code in (401, 302)

    async def test_streams_notification_event(self, application: Quart) -> None:
        """A notification pushed to the queue appears as a data frame in the stream."""
        event_payload = {"type": "like", "actor": "https://example.com/users/bob"}

        async with application.test_client() as client:
            await _login(client)

            # Push the event then a sentinel (None) so the generator terminates
            # immediately without waiting for the 25 s keep-alive timeout.
            queue: asyncio.Queue[Any] = application.config["NOTIFICATION_QUEUE"]
            await queue.put(event_payload)
            await queue.put(None)

            resp = await client.get("/admin/api/notifications/events")

        assert resp.status_code == 200
        assert "text/event-stream" in resp.content_type

        raw = await resp.get_data(as_text=True)

        # The stream must contain the retry directive and the data frame.
        assert "retry: 3000" in raw
        assert f"data: {json.dumps(event_payload, ensure_ascii=False)}" in raw

    async def test_second_connection_returns_409(
        self, application: Quart, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A second SSE connection while one is already open returns 409 Conflict."""
        import app.admin.sse as sse_module

        # Simulate an already-open SSE connection by setting the guard flag.
        monkeypatch.setattr(sse_module, "_sse_connected", True)

        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/notifications/events")

        assert resp.status_code == 409
