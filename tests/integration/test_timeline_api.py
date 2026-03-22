"""Integration tests for the admin timeline API and compose/media endpoints.

Tests:
- ``GET /admin/api/csrf``: authentication required, returns token.
- ``GET /admin/api/timeline``: authentication, response shape, own notes in
  timeline, ``since``/``before`` cursor pagination, invalid parameter handling.
- ``POST /admin/api/notes``: authentication, CSRF, missing body, successful
  publish, reply support, attachment linking (WP-14).
- ``POST /admin/api/media`` + ``POST /admin/api/notes`` with ``attachment_ids``:
  full upload → compose → attach flow.
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from PIL import Image
from quart import Quart
from werkzeug.datastructures import FileStorage

import app.admin.auth as auth_module
from app import create_app
from app.admin.auth import hash_password

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOCAL_DOMAIN = "test.example.com"
LOCAL_USERNAME = "testuser"


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------


def _make_jpeg(width: int = 8, height: int = 8) -> bytes:
    """Return minimal JPEG bytes."""
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _file(data: bytes, filename: str, content_type: str) -> FileStorage:
    """Wrap bytes in a FileStorage for multipart upload tests."""
    return FileStorage(stream=io.BytesIO(data), filename=filename, content_type=content_type)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def app(tmp_path: Any) -> AsyncGenerator[Quart, None]:
    """Test application with schema created and admin password seeded."""
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
            await db.commit()

        yield application

    auth_module._login_attempts.clear()


async def _login(client: Any) -> dict[str, str]:
    """Log in and return a ``{"X-CSRF-Token": "..."}`` header dict.

    The login form CSRF is scraped from GET /login; the admin API CSRF is
    taken from the session after successful authentication.

    Args:
        client: An entered Quart test client.

    Returns:
        A header dict ready to pass to admin API requests.
    """
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
# CSRF endpoint
# ---------------------------------------------------------------------------


class TestCsrfEndpoint:
    """Tests for ``GET /admin/api/csrf``."""

    async def test_unauthenticated_returns_401(self, app: Quart) -> None:
        """Unauthenticated request is rejected."""
        async with app.test_client() as client:
            resp = await client.get("/admin/api/csrf")
        assert resp.status_code in (401, 302)

    async def test_returns_csrf_token(self, app: Quart) -> None:
        """Authenticated request returns a non-empty CSRF token."""
        async with app.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/csrf")

        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert "csrf_token" in data
        assert isinstance(data["csrf_token"], str)
        assert len(data["csrf_token"]) > 0


# ---------------------------------------------------------------------------
# Timeline endpoint — authentication
# ---------------------------------------------------------------------------


class TestTimelineAuth:
    """Tests for authentication on ``GET /admin/api/timeline``."""

    async def test_unauthenticated_returns_401(self, app: Quart) -> None:
        """Unauthenticated request is rejected."""
        async with app.test_client() as client:
            resp = await client.get("/admin/api/timeline")
        assert resp.status_code in (401, 302)

    async def test_authenticated_returns_200(self, app: Quart) -> None:
        """Authenticated request returns 200 OK."""
        async with app.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/timeline")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Timeline endpoint — response shape
# ---------------------------------------------------------------------------


class TestTimelineResponseShape:
    """Tests for the JSON response structure of ``GET /admin/api/timeline``."""

    async def test_empty_timeline_shape(self, app: Quart) -> None:
        """Empty timeline returns correct envelope keys with empty data list."""
        async with app.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/timeline")
        data = json.loads(await resp.get_data())
        assert "data" in data
        assert "cursor" in data
        assert "has_more" in data
        assert isinstance(data["data"], list)
        assert isinstance(data["has_more"], bool)

    async def test_item_shape(self, app: Quart) -> None:
        """Each timeline item contains all required fields."""
        # Publish a note so the timeline is non-empty.
        async with app.test_client() as client:
            headers = await _login(client)
            await client.post(
                "/admin/api/notes",
                headers={**headers, "Content-Type": "application/json"},
                data=json.dumps({"body": "Hello from test"}),
            )
            resp = await client.get("/admin/api/timeline")
        data = json.loads(await resp.get_data())
        assert len(data["data"]) >= 1

        item = data["data"][0]
        required_keys = {
            "id",
            "type",
            "post_id",
            "author_name",
            "author_handle",
            "author_avatar",
            "published",
            "body_html",
            "media_url",
            "liked",
            "reposted",
            "own",
        }
        assert required_keys.issubset(item.keys())


# ---------------------------------------------------------------------------
# Timeline endpoint — own notes appear in timeline
# ---------------------------------------------------------------------------


class TestTimelineOwnNotes:
    """Tests for own notes appearing in ``GET /admin/api/timeline``."""

    async def test_own_note_appears_in_timeline(self, app: Quart) -> None:
        """A note published via the API appears in the timeline."""
        async with app.test_client() as client:
            headers = await _login(client)
            create_resp = await client.post(
                "/admin/api/notes",
                headers={**headers, "Content-Type": "application/json"},
                data=json.dumps({"body": "Timeline test post"}),
            )
            assert create_resp.status_code == 201

            tl_resp = await client.get("/admin/api/timeline")
        tl = json.loads(await tl_resp.get_data())
        assert any("Timeline test post" in (item.get("body_html") or "") for item in tl["data"])

    async def test_own_note_has_own_flag(self, app: Quart) -> None:
        """Own notes in the timeline carry ``"own": true``."""
        async with app.test_client() as client:
            headers = await _login(client)
            await client.post(
                "/admin/api/notes",
                headers={**headers, "Content-Type": "application/json"},
                data=json.dumps({"body": "Own flag test"}),
            )
            tl_resp = await client.get("/admin/api/timeline")
        tl = json.loads(await tl_resp.get_data())
        own_items = [i for i in tl["data"] if i.get("own")]
        assert len(own_items) >= 1

    async def test_own_note_has_internal_id(self, app: Quart) -> None:
        """Own notes carry ``internal_id`` for edit/delete operations."""
        async with app.test_client() as client:
            headers = await _login(client)
            await client.post(
                "/admin/api/notes",
                headers={**headers, "Content-Type": "application/json"},
                data=json.dumps({"body": "Internal ID test"}),
            )
            tl_resp = await client.get("/admin/api/timeline")
        tl = json.loads(await tl_resp.get_data())
        own = next(i for i in tl["data"] if i.get("own"))
        assert own.get("internal_id") is not None
        assert len(own["internal_id"]) > 0


# ---------------------------------------------------------------------------
# Timeline endpoint — pagination (since / before)
# ---------------------------------------------------------------------------


class TestTimelinePagination:
    """Tests for cursor-based pagination on ``GET /admin/api/timeline``."""

    async def test_since_returns_only_newer_items(self, app: Quart) -> None:
        """``?since=<ts>`` only returns items published after the timestamp."""
        # Use a timestamp in the future — nothing should be newer than it.
        future_ts = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        async with app.test_client() as client:
            headers = await _login(client)
            await client.post(
                "/admin/api/notes",
                headers={**headers, "Content-Type": "application/json"},
                data=json.dumps({"body": "First post"}),
            )
            poll_resp = await client.get(f"/admin/api/timeline?since={future_ts}")
        poll = json.loads(await poll_resp.get_data())
        assert poll_resp.status_code == 200
        assert poll["data"] == []

    async def test_before_returns_only_older_items(self, app: Quart) -> None:
        """``?before=<ts>`` only returns items published before the timestamp."""
        future_ts = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        async with app.test_client() as client:
            headers = await _login(client)
            await client.post(
                "/admin/api/notes",
                headers={**headers, "Content-Type": "application/json"},
                data=json.dumps({"body": "Before cursor test"}),
            )
            resp = await client.get(f"/admin/api/timeline?before={future_ts}")
        data = json.loads(await resp.get_data())
        assert resp.status_code == 200
        assert len(data["data"]) >= 1

    async def test_invalid_since_returns_400(self, app: Quart) -> None:
        """Malformed ``?since`` parameter returns 400 Bad Request."""
        async with app.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/timeline?since=not-a-date")
        assert resp.status_code == 400
        data = json.loads(await resp.get_data())
        assert "error" in data

    async def test_invalid_before_returns_400(self, app: Quart) -> None:
        """Malformed ``?before`` parameter returns 400 Bad Request."""
        async with app.test_client() as client:
            await _login(client)
            resp = await client.get("/admin/api/timeline?before=not-a-date")
        assert resp.status_code == 400
        data = json.loads(await resp.get_data())
        assert "error" in data

    async def test_cursor_is_published_of_last_item(self, app: Quart) -> None:
        """``cursor`` in the response matches the last item's ``published`` field."""
        async with app.test_client() as client:
            headers = await _login(client)
            await client.post(
                "/admin/api/notes",
                headers={**headers, "Content-Type": "application/json"},
                data=json.dumps({"body": "Cursor check"}),
            )
            resp = await client.get("/admin/api/timeline")
        tl = json.loads(await resp.get_data())
        assert tl["cursor"] == tl["data"][-1]["published"]


# ---------------------------------------------------------------------------
# Notes endpoint — authentication and CSRF
# ---------------------------------------------------------------------------


class TestNotesAuth:
    """Tests for auth and CSRF on ``POST /admin/api/notes``."""

    async def test_unauthenticated_returns_401(self, app: Quart) -> None:
        """Unauthenticated request is rejected."""
        async with app.test_client() as client:
            resp = await client.post(
                "/admin/api/notes",
                headers={"Content-Type": "application/json"},
                data=json.dumps({"body": "test"}),
            )
        assert resp.status_code in (401, 302)

    async def test_missing_csrf_returns_403(self, app: Quart) -> None:
        """Authenticated but missing CSRF token returns 403."""
        async with app.test_client() as client:
            await _login(client)
            resp = await client.post(
                "/admin/api/notes",
                headers={"Content-Type": "application/json"},
                data=json.dumps({"body": "test"}),
            )
        assert resp.status_code == 403

    async def test_wrong_csrf_returns_403(self, app: Quart) -> None:
        """Wrong CSRF token returns 403."""
        async with app.test_client() as client:
            await _login(client)
            resp = await client.post(
                "/admin/api/notes",
                headers={"Content-Type": "application/json", "X-CSRF-Token": "wrong"},
                data=json.dumps({"body": "test"}),
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Notes endpoint — compose flow
# ---------------------------------------------------------------------------


class TestNotesCompose:
    """Tests for the compose flow on ``POST /admin/api/notes``."""

    async def test_missing_body_returns_400(self, app: Quart) -> None:
        """Request without ``body`` field returns 400."""
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/notes",
                headers={**headers, "Content-Type": "application/json"},
                data=json.dumps({}),
            )
        assert resp.status_code == 400

    async def test_empty_body_returns_400(self, app: Quart) -> None:
        """Request with whitespace-only ``body`` returns 400."""
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/notes",
                headers={**headers, "Content-Type": "application/json"},
                data=json.dumps({"body": "   "}),
            )
        assert resp.status_code == 400

    async def test_successful_publish_returns_201(self, app: Quart) -> None:
        """Valid note returns 201 with ``id`` and ``ap_id``."""
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/notes",
                headers={**headers, "Content-Type": "application/json"},
                data=json.dumps({"body": "Hello fediverse"}),
            )
        assert resp.status_code == 201
        data = json.loads(await resp.get_data())
        assert "id" in data
        assert "ap_id" in data
        assert data["ap_id"].startswith(f"https://{LOCAL_DOMAIN}/")

    async def test_reply_to_accepted(self, app: Quart) -> None:
        """Note with ``in_reply_to`` is accepted and returns 201."""
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/notes",
                headers={**headers, "Content-Type": "application/json"},
                data=json.dumps(
                    {
                        "body": "My reply",
                        "in_reply_to": "https://remote.example/notes/abc123",
                    }
                ),
            )
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Media + compose — attachment linking (WP-14)
# ---------------------------------------------------------------------------


class TestAttachmentFlow:
    """Tests for the full upload → compose → attach flow (WP-14)."""

    async def test_upload_then_compose_with_attachment(self, app: Quart) -> None:
        """Uploading an image and referencing its ID in a note links the attachment."""
        async with app.test_client() as client:
            headers = await _login(client)

            # Step 1: upload a JPEG.
            jpeg_data = _make_jpeg()
            upload_resp = await client.post(
                "/admin/api/media",
                headers=headers,
                files={"file": _file(jpeg_data, "test.jpg", "image/jpeg")},
            )
            assert upload_resp.status_code == 201
            upload = json.loads(await upload_resp.get_data())
            attachment_id = upload["id"]
            assert attachment_id

            # Step 2: compose a note referencing the attachment.
            note_resp = await client.post(
                "/admin/api/notes",
                headers={**headers, "Content-Type": "application/json"},
                data=json.dumps(
                    {
                        "body": "Post with image",
                        "attachment_ids": [attachment_id],
                    }
                ),
            )
            assert note_resp.status_code == 201

    async def test_timeline_item_has_media_url_after_attach(self, app: Quart) -> None:
        """After uploading and attaching, the note appears in the timeline with ``media_url``."""
        async with app.test_client() as client:
            headers = await _login(client)

            jpeg_data = _make_jpeg()
            upload_resp = await client.post(
                "/admin/api/media",
                headers=headers,
                files={"file": _file(jpeg_data, "test.jpg", "image/jpeg")},
            )
            upload = json.loads(await upload_resp.get_data())
            attachment_id = upload["id"]

            await client.post(
                "/admin/api/notes",
                headers={**headers, "Content-Type": "application/json"},
                data=json.dumps(
                    {
                        "body": "Post with media",
                        "attachment_ids": [attachment_id],
                    }
                ),
            )

            tl_resp = await client.get("/admin/api/timeline")
        tl = json.loads(await tl_resp.get_data())
        media_items = [i for i in tl["data"] if i.get("media_url")]
        assert len(media_items) >= 1
        assert media_items[0]["media_url"].startswith("/media/")

    async def test_invalid_attachment_id_is_ignored(self, app: Quart) -> None:
        """Non-UUID or unknown attachment IDs are silently skipped; note is still created."""
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/notes",
                headers={**headers, "Content-Type": "application/json"},
                data=json.dumps(
                    {
                        "body": "Post with bad attach ID",
                        "attachment_ids": ["not-a-uuid", "00000000-0000-0000-0000-000000000000"],
                    }
                ),
            )
        assert resp.status_code == 201

    async def test_upload_not_associated_before_attach(self, app: Quart) -> None:
        """An uploaded attachment without a note has ``media_url`` in upload response."""
        async with app.test_client() as client:
            headers = await _login(client)
            jpeg_data = _make_jpeg()
            upload_resp = await client.post(
                "/admin/api/media",
                headers=headers,
                files={"file": _file(jpeg_data, "img.jpg", "image/jpeg")},
            )
        assert upload_resp.status_code == 201
        upload = json.loads(await upload_resp.get_data())
        assert upload["url"].startswith("/media/")
        assert "id" in upload
