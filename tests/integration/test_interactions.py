"""Integration tests for admin interaction API endpoints (WP-15).

Tests:
- ``POST /admin/api/likes``: auth, CSRF, missing body, successful like, idempotency.
- ``POST /admin/api/unlikes``: auth, CSRF, missing body, successful unlike, idempotency.
- ``POST /admin/api/boosts``: auth, CSRF, missing body, successful boost, idempotency.
- ``POST /admin/api/unboosts``: auth, CSRF, missing body, successful unboost, idempotency.
- ``PATCH /admin/api/notes/<id>``: auth, CSRF, invalid ID, missing body, 404, successful edit,
  body persisted to database.
- ``DELETE /admin/api/notes/<id>``: auth, CSRF, invalid ID, 404, successful delete,
  note removed from database.
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
from app.models.boost import Boost
from app.models.like import Like
from app.models.timeline_item import TimelineItem

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOCAL_DOMAIN = "test.example.com"
LOCAL_USERNAME = "testuser"
REMOTE_POST_URI = "https://remote.example.com/notes/abc123"
REMOTE_ACTOR_URI = "https://remote.example.com/users/alice"
REMOTE_INBOX = "https://remote.example.com/users/alice/inbox"
_JSON = "application/json"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def app(tmp_path: Any) -> AsyncGenerator[Quart, None]:
    """Test application with schema, admin password, and a seeded timeline item."""
    os.environ["TINKER_DOMAIN"] = LOCAL_DOMAIN
    os.environ["TINKER_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["TINKER_MEDIA_PATH"] = str(tmp_path / "media")
    os.environ["TINKER_SECRET_KEY"] = "test-secret-key"
    os.environ["TINKER_USERNAME"] = LOCAL_USERNAME

    application = create_app()

    from sqlalchemy import create_engine

    from app.models.base import Base

    sync_engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    auth_module._login_attempts.clear()

    async with application.test_app():
        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            from app.services.settings import SettingsService

            svc = SettingsService(db)
            await svc.set_admin_password_hash(hash_password("pass"))

            # Seed a RemoteActor cache entry so _find_inbox_for_post resolves.
            from app.models.remote_actor import RemoteActor

            actor = RemoteActor(
                uri=REMOTE_ACTOR_URI,
                handle="@alice@remote.example.com",
                inbox_url=REMOTE_INBOX,
                shared_inbox_url=None,
                public_key="",
            )
            db.add(actor)

            # Seed a timeline item linking the post URI to the actor URI.
            item = TimelineItem(
                activity_type="Create",
                actor_uri=REMOTE_ACTOR_URI,
                actor_name="Alice",
                original_object_uri=REMOTE_POST_URI,
                content_html="<p>Hello</p>",
            )
            db.add(item)
            await db.commit()

        yield application

    auth_module._login_attempts.clear()


async def _login(client: Any) -> dict[str, str]:
    """Log in and return a ``{"X-CSRF-Token": "..."}`` header dict."""
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
# DB assertion helpers
# ---------------------------------------------------------------------------


async def _count_likes(app: Quart, note_uri: str) -> int:
    """Count Like rows for a given note URI."""
    from sqlalchemy import select

    session_factory = app.config["DB_SESSION_FACTORY"]
    async with session_factory() as db:
        result = await db.execute(select(Like).where(Like.note_uri == note_uri))
        return len(result.scalars().all())


async def _count_boosts(app: Quart, note_uri: str) -> int:
    """Count Boost rows for a given note URI."""
    from sqlalchemy import select

    session_factory = app.config["DB_SESSION_FACTORY"]
    async with session_factory() as db:
        result = await db.execute(select(Boost).where(Boost.note_uri == note_uri))
        return len(result.scalars().all())


async def _create_note(client: Any, headers: dict[str, str], body: str = "Test note") -> str:
    """Create a note via the API and return its internal UUID string."""
    resp = await client.post(
        "/admin/api/notes",
        headers={**headers, "Content-Type": _JSON},
        data=json.dumps({"body": body}),
    )
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}"
    data = json.loads(await resp.get_data())
    return str(data["id"])


# ---------------------------------------------------------------------------
# POST /admin/api/likes
# ---------------------------------------------------------------------------


class TestLikePost:
    """Tests for ``POST /admin/api/likes``."""

    async def test_unauthenticated_returns_401(self, app: Quart) -> None:
        async with app.test_client() as client:
            resp = await client.post(
                "/admin/api/likes",
                headers={"Content-Type": _JSON},
                data=json.dumps({"post_id": REMOTE_POST_URI}),
            )
        assert resp.status_code in (401, 302)

    async def test_missing_csrf_returns_403(self, app: Quart) -> None:
        async with app.test_client() as client:
            await _login(client)
            resp = await client.post(
                "/admin/api/likes",
                headers={"Content-Type": _JSON},
                data=json.dumps({"post_id": REMOTE_POST_URI}),
            )
        assert resp.status_code == 403

    async def test_missing_post_id_returns_400(self, app: Quart) -> None:
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/likes",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({}),
            )
        assert resp.status_code == 400

    async def test_successful_like_returns_200(self, app: Quart) -> None:
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/likes",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({"post_id": REMOTE_POST_URI}),
            )
        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert data["status"] == "ok"

    async def test_successful_like_stores_record(self, app: Quart) -> None:
        async with app.test_client() as client:
            headers = await _login(client)
            await client.post(
                "/admin/api/likes",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({"post_id": REMOTE_POST_URI}),
            )
        assert await _count_likes(app, REMOTE_POST_URI) == 1

    async def test_like_is_idempotent(self, app: Quart) -> None:
        async with app.test_client() as client:
            headers = await _login(client)
            for _ in range(2):
                resp = await client.post(
                    "/admin/api/likes",
                    headers={**headers, "Content-Type": _JSON},
                    data=json.dumps({"post_id": REMOTE_POST_URI}),
                )
            assert resp.status_code == 200
        assert await _count_likes(app, REMOTE_POST_URI) == 1


# ---------------------------------------------------------------------------
# POST /admin/api/unlikes
# ---------------------------------------------------------------------------


class TestUnlikePost:
    """Tests for ``POST /admin/api/unlikes``."""

    async def test_unauthenticated_returns_401(self, app: Quart) -> None:
        async with app.test_client() as client:
            resp = await client.post(
                "/admin/api/unlikes",
                headers={"Content-Type": _JSON},
                data=json.dumps({"post_id": REMOTE_POST_URI}),
            )
        assert resp.status_code in (401, 302)

    async def test_missing_csrf_returns_403(self, app: Quart) -> None:
        async with app.test_client() as client:
            await _login(client)
            resp = await client.post(
                "/admin/api/unlikes",
                headers={"Content-Type": _JSON},
                data=json.dumps({"post_id": REMOTE_POST_URI}),
            )
        assert resp.status_code == 403

    async def test_missing_post_id_returns_400(self, app: Quart) -> None:
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/unlikes",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({}),
            )
        assert resp.status_code == 400

    async def test_unlike_not_liked_is_idempotent(self, app: Quart) -> None:
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/unlikes",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({"post_id": REMOTE_POST_URI}),
            )
        assert resp.status_code == 200

    async def test_unlike_removes_like_record(self, app: Quart) -> None:
        async with app.test_client() as client:
            headers = await _login(client)
            await client.post(
                "/admin/api/likes",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({"post_id": REMOTE_POST_URI}),
            )
            assert await _count_likes(app, REMOTE_POST_URI) == 1
            resp = await client.post(
                "/admin/api/unlikes",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({"post_id": REMOTE_POST_URI}),
            )
        assert resp.status_code == 200
        assert await _count_likes(app, REMOTE_POST_URI) == 0

    async def test_unlike_is_idempotent_after_removal(self, app: Quart) -> None:
        async with app.test_client() as client:
            headers = await _login(client)
            await client.post(
                "/admin/api/likes",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({"post_id": REMOTE_POST_URI}),
            )
            for _ in range(2):
                resp = await client.post(
                    "/admin/api/unlikes",
                    headers={**headers, "Content-Type": _JSON},
                    data=json.dumps({"post_id": REMOTE_POST_URI}),
                )
        assert resp.status_code == 200
        assert await _count_likes(app, REMOTE_POST_URI) == 0


# ---------------------------------------------------------------------------
# POST /admin/api/boosts
# ---------------------------------------------------------------------------


class TestBoostPost:
    """Tests for ``POST /admin/api/boosts``."""

    async def test_unauthenticated_returns_401(self, app: Quart) -> None:
        async with app.test_client() as client:
            resp = await client.post(
                "/admin/api/boosts",
                headers={"Content-Type": _JSON},
                data=json.dumps({"post_id": REMOTE_POST_URI}),
            )
        assert resp.status_code in (401, 302)

    async def test_missing_csrf_returns_403(self, app: Quart) -> None:
        async with app.test_client() as client:
            await _login(client)
            resp = await client.post(
                "/admin/api/boosts",
                headers={"Content-Type": _JSON},
                data=json.dumps({"post_id": REMOTE_POST_URI}),
            )
        assert resp.status_code == 403

    async def test_missing_post_id_returns_400(self, app: Quart) -> None:
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/boosts",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({}),
            )
        assert resp.status_code == 400

    async def test_successful_boost_returns_200(self, app: Quart) -> None:
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/boosts",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({"post_id": REMOTE_POST_URI}),
            )
        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert data["status"] == "ok"

    async def test_successful_boost_stores_record(self, app: Quart) -> None:
        async with app.test_client() as client:
            headers = await _login(client)
            await client.post(
                "/admin/api/boosts",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({"post_id": REMOTE_POST_URI}),
            )
        assert await _count_boosts(app, REMOTE_POST_URI) == 1

    async def test_boost_is_idempotent(self, app: Quart) -> None:
        async with app.test_client() as client:
            headers = await _login(client)
            for _ in range(2):
                resp = await client.post(
                    "/admin/api/boosts",
                    headers={**headers, "Content-Type": _JSON},
                    data=json.dumps({"post_id": REMOTE_POST_URI}),
                )
            assert resp.status_code == 200
        assert await _count_boosts(app, REMOTE_POST_URI) == 1


# ---------------------------------------------------------------------------
# POST /admin/api/unboosts
# ---------------------------------------------------------------------------


class TestUnboostPost:
    """Tests for ``POST /admin/api/unboosts``."""

    async def test_unauthenticated_returns_401(self, app: Quart) -> None:
        async with app.test_client() as client:
            resp = await client.post(
                "/admin/api/unboosts",
                headers={"Content-Type": _JSON},
                data=json.dumps({"post_id": REMOTE_POST_URI}),
            )
        assert resp.status_code in (401, 302)

    async def test_missing_csrf_returns_403(self, app: Quart) -> None:
        async with app.test_client() as client:
            await _login(client)
            resp = await client.post(
                "/admin/api/unboosts",
                headers={"Content-Type": _JSON},
                data=json.dumps({"post_id": REMOTE_POST_URI}),
            )
        assert resp.status_code == 403

    async def test_missing_post_id_returns_400(self, app: Quart) -> None:
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/unboosts",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({}),
            )
        assert resp.status_code == 400

    async def test_unboost_not_boosted_is_idempotent(self, app: Quart) -> None:
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/unboosts",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({"post_id": REMOTE_POST_URI}),
            )
        assert resp.status_code == 200

    async def test_unboost_removes_boost_record(self, app: Quart) -> None:
        async with app.test_client() as client:
            headers = await _login(client)
            await client.post(
                "/admin/api/boosts",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({"post_id": REMOTE_POST_URI}),
            )
            assert await _count_boosts(app, REMOTE_POST_URI) == 1
            resp = await client.post(
                "/admin/api/unboosts",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({"post_id": REMOTE_POST_URI}),
            )
        assert resp.status_code == 200
        assert await _count_boosts(app, REMOTE_POST_URI) == 0


# ---------------------------------------------------------------------------
# PATCH /admin/api/notes/<id> — edit own note
# ---------------------------------------------------------------------------


class TestEditNote:
    """Tests for ``PATCH /admin/api/notes/<id>``."""

    async def test_unauthenticated_returns_401(self, app: Quart) -> None:
        """Request without a session is rejected before CSRF is checked."""
        async with app.test_client() as client:
            resp = await client.patch(
                "/admin/api/notes/00000000-0000-0000-0000-000000000001",
                headers={"Content-Type": _JSON},
                data=json.dumps({"body": "edited"}),
            )
        assert resp.status_code in (401, 302)

    async def test_missing_csrf_returns_403(self, app: Quart) -> None:
        """Authenticated request without X-CSRF-Token header returns 403."""
        async with app.test_client() as client:
            await _login(client)
            resp = await client.patch(
                "/admin/api/notes/00000000-0000-0000-0000-000000000001",
                headers={"Content-Type": _JSON},
                data=json.dumps({"body": "edited"}),
            )
        assert resp.status_code == 403

    async def test_invalid_note_id_returns_400(self, app: Quart) -> None:
        """A non-UUID note ID in the path returns 400."""
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.patch(
                "/admin/api/notes/not-a-uuid",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({"body": "edited"}),
            )
        assert resp.status_code == 400

    async def test_missing_body_returns_400(self, app: Quart) -> None:
        """Request with no ``body`` field returns 400."""
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.patch(
                "/admin/api/notes/00000000-0000-0000-0000-000000000001",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({}),
            )
        assert resp.status_code == 400

    async def test_nonexistent_note_returns_404(self, app: Quart) -> None:
        """A valid UUID that doesn't match any note returns 404."""
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.patch(
                "/admin/api/notes/00000000-0000-0000-0000-000000000001",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({"body": "edited"}),
            )
        assert resp.status_code == 404

    async def test_successful_edit_returns_200_with_ids(self, app: Quart) -> None:
        """A valid edit returns 200 with ``id`` and ``ap_id`` fields."""
        async with app.test_client() as client:
            headers = await _login(client)
            note_id = await _create_note(client, headers)
            resp = await client.patch(
                f"/admin/api/notes/{note_id}",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({"body": "Edited content"}),
            )
        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert data["id"] == note_id
        assert "ap_id" in data

    async def test_edit_persists_new_body(self, app: Quart) -> None:
        """After a successful edit, the note's body is updated in the database."""
        import uuid

        from sqlalchemy import select

        from app.models.note import Note

        async with app.test_client() as client:
            headers = await _login(client)
            note_id = await _create_note(client, headers, body="Original body")
            await client.patch(
                f"/admin/api/notes/{note_id}",
                headers={**headers, "Content-Type": _JSON},
                data=json.dumps({"body": "Edited body"}),
            )

        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            result = await db.execute(select(Note).where(Note.id == uuid.UUID(note_id)))
            note = result.scalars().first()
        assert note is not None
        assert note.body == "Edited body"


# ---------------------------------------------------------------------------
# DELETE /admin/api/notes/<id> — delete own note
# ---------------------------------------------------------------------------


class TestDeleteNote:
    """Tests for ``DELETE /admin/api/notes/<id>``."""

    async def test_unauthenticated_returns_401(self, app: Quart) -> None:
        """Request without a session is rejected before CSRF is checked."""
        async with app.test_client() as client:
            resp = await client.delete(
                "/admin/api/notes/00000000-0000-0000-0000-000000000001",
            )
        assert resp.status_code in (401, 302)

    async def test_missing_csrf_returns_403(self, app: Quart) -> None:
        """Authenticated request without X-CSRF-Token header returns 403."""
        async with app.test_client() as client:
            await _login(client)
            resp = await client.delete(
                "/admin/api/notes/00000000-0000-0000-0000-000000000001",
            )
        assert resp.status_code == 403

    async def test_invalid_note_id_returns_400(self, app: Quart) -> None:
        """A non-UUID note ID in the path returns 400."""
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.delete(
                "/admin/api/notes/not-a-uuid",
                headers=headers,
            )
        assert resp.status_code == 400

    async def test_nonexistent_note_returns_404(self, app: Quart) -> None:
        """A valid UUID that doesn't match any note returns 404."""
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.delete(
                "/admin/api/notes/00000000-0000-0000-0000-000000000001",
                headers=headers,
            )
        assert resp.status_code == 404

    async def test_successful_delete_returns_204(self, app: Quart) -> None:
        """Deleting an existing note returns 204 No Content."""
        async with app.test_client() as client:
            headers = await _login(client)
            note_id = await _create_note(client, headers)
            resp = await client.delete(
                f"/admin/api/notes/{note_id}",
                headers=headers,
            )
        assert resp.status_code == 204

    async def test_delete_removes_note_from_db(self, app: Quart) -> None:
        """After a successful delete, the note no longer exists in the database."""
        import uuid

        from sqlalchemy import select

        from app.models.note import Note

        async with app.test_client() as client:
            headers = await _login(client)
            note_id = await _create_note(client, headers, body="Will be deleted")
            await client.delete(
                f"/admin/api/notes/{note_id}",
                headers=headers,
            )

        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            result = await db.execute(select(Note).where(Note.id == uuid.UUID(note_id)))
            note = result.scalars().first()
        assert note is None
