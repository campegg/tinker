"""Integration tests for the likes list API endpoint (WP-18).

Tests:
- ``GET /admin/api/likes``: auth guard, empty state, liked posts returned,
  uncached posts skipped, pagination.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from quart import Quart

import app.admin.auth as auth_module
from app import create_app
from app.admin.auth import hash_password
from app.core.config import PAGE_SIZE

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOCAL_DOMAIN = "test.example.com"
LOCAL_USERNAME = "testuser"
REMOTE_NOTE_URI = "https://remote.example.com/notes/1"
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


def _local_actor_uri() -> str:
    return f"https://{LOCAL_DOMAIN}/{LOCAL_USERNAME}"


def _seed_like(db: Any, note_uri: str, **kwargs: Any) -> Any:
    """Create and return a Like model instance."""
    from app.models.like import Like

    defaults: dict[str, Any] = {
        "actor_uri": _local_actor_uri(),
        "note_uri": note_uri,
    }
    defaults.update(kwargs)
    row = Like(**defaults)
    db.add(row)
    return row


def _seed_timeline_item(db: Any, note_uri: str, **kwargs: Any) -> Any:
    """Create and return a TimelineItem model instance with a cached note."""
    from app.models.timeline_item import TimelineItem

    defaults: dict[str, Any] = {
        "original_object_uri": note_uri,
        "actor_uri": REMOTE_ACTOR_URI,
        "actor_name": "Alice",
        "content_html": "<p>Hello world</p>",
        "activity_type": "Create",
    }
    defaults.update(kwargs)
    row = TimelineItem(**defaults)
    db.add(row)
    return row


# ---------------------------------------------------------------------------
# GET /admin/api/likes
# ---------------------------------------------------------------------------


class TestListLikes:
    """Tests for ``GET /admin/api/likes``."""

    async def test_requires_auth(self, application: Quart) -> None:
        """Unauthenticated requests are redirected or receive 401."""
        async with application.test_client() as client:
            resp = await client.get("/admin/api/likes")
        assert resp.status_code in (401, 302)

    async def test_empty_state(self, application: Quart) -> None:
        """Returns an empty data array when the user hasn't liked anything."""
        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/likes")

        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert data["data"] == []
        assert data["cursor"] is None
        assert data["has_more"] is False

    async def test_returns_liked_posts(self, application: Quart) -> None:
        """Returns liked posts that have cached timeline content."""
        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            _seed_like(db, REMOTE_NOTE_URI)
            _seed_timeline_item(db, REMOTE_NOTE_URI)
            await db.commit()

        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/likes")

        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert len(data["data"]) == 1
        item = data["data"][0]
        assert item["post_id"] == REMOTE_NOTE_URI
        assert item["liked"] is True
        assert "<p>Hello world</p>" in item["body_html"]
        assert "author_name" in item
        assert "author_handle" in item
        assert "published" in item

    async def test_skips_uncached_posts(self, application: Quart) -> None:
        """Liked posts with no cached TimelineItem are omitted from the response."""
        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            # Like with no corresponding TimelineItem.
            _seed_like(db, "https://remote.example.com/notes/uncached")
            await db.commit()

        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/likes")

        data = json.loads(await resp.get_data())
        assert data["data"] == []

    async def test_pagination(self, application: Quart) -> None:
        """First page returns PAGE_SIZE items and ``has_more=True`` when PAGE_SIZE+5 exist."""
        base_time = datetime(2026, 1, 1, tzinfo=UTC)
        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            for i in range(PAGE_SIZE + 5):
                note_uri = f"https://remote.example.com/notes/{i}"
                _seed_like(db, note_uri, created_at=base_time + timedelta(seconds=i))
                _seed_timeline_item(db, note_uri)
            await db.commit()

        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/likes")

        payload = json.loads(await resp.get_data())
        assert len(payload["data"]) == PAGE_SIZE
        assert payload["has_more"] is True
        assert payload["cursor"] is not None

    async def test_invalid_before_returns_400(self, application: Quart) -> None:
        """An unparseable ``before`` parameter returns 400."""
        async with application.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/likes?before=not-a-date")
        assert resp.status_code == 400
