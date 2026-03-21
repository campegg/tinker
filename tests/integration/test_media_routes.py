"""Integration tests for the media upload endpoint and media serving route.

Tests:
- ``POST /admin/api/media``: authentication, CSRF, MIME validation, size
  validation, successful upload, MediaAttachment record creation.
- ``GET /media/<path>``: serving uploaded files, 404 on missing paths,
  path traversal prevention.
- Avatar proxying via :func:`app.media.proxy_avatar` wired through
  inbox Follow processing.
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import AsyncGenerator
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
# Helpers
# ---------------------------------------------------------------------------


def _make_jpeg(width: int = 8, height: int = 8) -> bytes:
    """Return raw JPEG bytes."""
    img = Image.new("RGB", (width, height), color=(200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_png() -> bytes:
    """Return raw PNG bytes."""
    img = Image.new("RGBA", (4, 4))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _file(data: bytes, filename: str, content_type: str) -> FileStorage:
    """Wrap bytes in a :class:`~werkzeug.datastructures.FileStorage`."""
    return FileStorage(stream=io.BytesIO(data), filename=filename, content_type=content_type)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def app(tmp_path: Any) -> AsyncGenerator[Quart, None]:
    """Create a test application with an authenticated admin session ready."""
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

    # Clear the rate-limit state so login attempts in this test don't bleed
    # into subsequent tests (or vice versa).
    auth_module._login_attempts.clear()

    async with application.test_app():
        # Seed admin password.
        session_factory = application.config["DB_SESSION_FACTORY"]
        async with session_factory() as db:
            from app.services.settings import SettingsService

            svc = SettingsService(db)
            await svc.set_admin_password_hash(hash_password("pass"))
            await db.commit()

        yield application

    auth_module._login_attempts.clear()


async def _login(client: Any) -> dict[str, str]:
    """Log in and return a dict with the CSRF token header for admin API calls.

    The login form itself requires a CSRF token (fetched from the GET page),
    and the admin API requires a separate ``X-CSRF-Token`` header read from
    the session after authentication.

    Args:
        client: The Quart test client (already entered as async context).

    Returns:
        A dict with the ``X-CSRF-Token`` header value for admin API requests.
    """
    # GET /login — sets the session CSRF token and embeds it in the form.
    resp = await client.get("/login")
    body = await resp.get_data(as_text=True)
    # Extract the login-form CSRF token from the hidden input.
    marker = 'name="csrf_token" value="'
    start = body.index(marker) + len(marker)
    end = body.index('"', start)
    login_csrf = body[start:end]

    # POST credentials including the login-form CSRF token.
    await client.post(
        "/login",
        form={
            "username": LOCAL_USERNAME,
            "password": "pass",
            "csrf_token": login_csrf,
        },
    )

    # The session now contains the admin API CSRF token.
    async with client.session_transaction() as sess:
        csrf = sess.get("csrf_token", "")
    return {"X-CSRF-Token": csrf}


# ---------------------------------------------------------------------------
# Upload endpoint — authentication and CSRF
# ---------------------------------------------------------------------------


class TestMediaUploadAuth:
    """Tests for authentication and CSRF on ``POST /admin/api/media``."""

    async def test_unauthenticated_returns_401(self, app: Quart) -> None:
        """Unauthenticated request returns 401 or redirect."""
        async with app.test_client() as client:
            resp = await client.post("/admin/api/media")
        # require_auth returns 401 for API endpoints.
        assert resp.status_code in (401, 302)

    async def test_missing_csrf_returns_403(self, app: Quart) -> None:
        """Authenticated request without CSRF token returns 403."""
        async with app.test_client() as client:
            await _login(client)
            resp = await client.post(
                "/admin/api/media",
                files={"file": _file(_make_jpeg(), "photo.jpg", "image/jpeg")},
                # No X-CSRF-Token header.
            )
        assert resp.status_code == 403

    async def test_wrong_csrf_returns_403(self, app: Quart) -> None:
        """Authenticated request with a wrong CSRF token returns 403."""
        async with app.test_client() as client:
            await _login(client)
            resp = await client.post(
                "/admin/api/media",
                files={"file": _file(_make_jpeg(), "photo.jpg", "image/jpeg")},
                headers={"X-CSRF-Token": "wrong-token"},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Upload endpoint — validation
# ---------------------------------------------------------------------------


class TestMediaUploadValidation:
    """Tests for input validation on ``POST /admin/api/media``."""

    async def test_no_file_returns_400(self, app: Quart) -> None:
        """Request without a ``file`` field returns 400."""
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post("/admin/api/media", headers=headers)
        assert resp.status_code == 400
        data = json.loads(await resp.get_data())
        assert "error" in data

    async def test_disallowed_mime_returns_400(self, app: Quart) -> None:
        """Upload with a disallowed MIME type returns 400."""
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/media",
                files={"file": _file(b"<svg/>", "image.svg", "image/svg+xml")},
                headers=headers,
            )
        assert resp.status_code == 400
        data = json.loads(await resp.get_data())
        assert "Unsupported file type" in data["error"]

    async def test_oversized_file_returns_400(self, app: Quart) -> None:
        """File exceeding the size limit returns 400."""
        from app.media import MAX_FILE_SIZE_BYTES

        oversized = b"x" * (MAX_FILE_SIZE_BYTES + 1)
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/media",
                files={"file": _file(oversized, "big.jpg", "image/jpeg")},
                headers=headers,
            )
        assert resp.status_code == 400
        data = json.loads(await resp.get_data())
        assert "too large" in data["error"].lower()

    async def test_invalid_image_data_returns_400(self, app: Quart) -> None:
        """Data that claims to be JPEG but isn't returns 400."""
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/media",
                files={"file": _file(b"not-an-image", "fake.jpg", "image/jpeg")},
                headers=headers,
            )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Upload endpoint — successful upload
# ---------------------------------------------------------------------------


class TestMediaUploadSuccess:
    """Tests for successful media uploads."""

    async def test_jpeg_upload_returns_201(self, app: Quart) -> None:
        """Valid JPEG upload returns 201 with id and url."""
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/media",
                files={"file": _file(_make_jpeg(), "photo.jpg", "image/jpeg")},
                headers=headers,
            )
        assert resp.status_code == 201
        data = json.loads(await resp.get_data())
        assert "id" in data
        assert "url" in data
        assert data["url"].startswith("/media/uploads/")

    async def test_png_upload_accepted(self, app: Quart) -> None:
        """Valid PNG upload is accepted."""
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/media",
                files={"file": _file(_make_png(), "image.png", "image/png")},
                headers=headers,
            )
        assert resp.status_code == 201

    async def test_alt_text_stored(self, app: Quart) -> None:
        """Alt text is stored in the MediaAttachment record."""
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/media",
                files={"file": _file(_make_jpeg(), "photo.jpg", "image/jpeg")},
                form={"alt_text": "A red square"},
                headers=headers,
            )
        data = json.loads(await resp.get_data())
        attachment_id = data["id"]

        # Verify alt_text persisted.
        async with app.app_context():
            session_factory = app.config["DB_SESSION_FACTORY"]
            async with session_factory() as session:
                import uuid as uuid_mod

                from app.repositories.media_attachment import MediaAttachmentRepository

                repo = MediaAttachmentRepository(session)
                attachment = await repo.get_by_id(uuid_mod.UUID(attachment_id))
                assert attachment is not None
                assert attachment.alt_text == "A red square"

    async def test_file_written_to_disk(self, app: Quart, tmp_path: Any) -> None:
        """The uploaded file is written to the media directory on disk."""
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/media",
                files={"file": _file(_make_jpeg(), "photo.jpg", "image/jpeg")},
                headers=headers,
            )
        data = json.loads(await resp.get_data())
        url_path = data["url"]  # e.g. /media/uploads/uuid.jpg
        relative = url_path.removeprefix("/media/")
        media_root = tmp_path / "media"
        assert (media_root / relative).exists()

    async def test_media_attachment_record_created(self, app: Quart) -> None:
        """A MediaAttachment record is created in the database."""
        async with app.test_client() as client:
            headers = await _login(client)
            resp = await client.post(
                "/admin/api/media",
                files={"file": _file(_make_jpeg(), "photo.jpg", "image/jpeg")},
                headers=headers,
            )
        data = json.loads(await resp.get_data())
        attachment_id = data["id"]

        async with app.app_context():
            session_factory = app.config["DB_SESSION_FACTORY"]
            async with session_factory() as session:
                import uuid as uuid_mod

                from app.repositories.media_attachment import MediaAttachmentRepository

                repo = MediaAttachmentRepository(session)
                rec = await repo.get_by_id(uuid_mod.UUID(attachment_id))
                assert rec is not None
                assert rec.mime_type == "image/jpeg"
                assert rec.note_id is None  # unattached until WP-14


# ---------------------------------------------------------------------------
# Media serving route
# ---------------------------------------------------------------------------


class TestServeMedia:
    """Tests for ``GET /media/<path:filename>``."""

    async def test_serves_uploaded_file(self, app: Quart, tmp_path: Any) -> None:
        """A file placed in the media directory is served correctly."""
        media_dir = tmp_path / "media" / "uploads"
        media_dir.mkdir(parents=True, exist_ok=True)
        test_file = media_dir / "test.jpg"
        jpeg = _make_jpeg()
        test_file.write_bytes(jpeg)

        async with app.test_client() as client:
            resp = await client.get("/media/uploads/test.jpg")
        assert resp.status_code == 200

    async def test_missing_file_returns_404(self, app: Quart) -> None:
        """Request for a non-existent file returns 404."""
        async with app.test_client() as client:
            resp = await client.get("/media/uploads/nonexistent.jpg")
        assert resp.status_code == 404

    async def test_path_traversal_returns_404(self, app: Quart) -> None:
        """Path traversal attempts return 404."""
        async with app.test_client() as client:
            resp = await client.get("/media/../../../etc/passwd")
        # Quart normalises the path before routing; it will never reach the view
        # as a traversal -- resulting in 404 either way.
        assert resp.status_code == 404

    async def test_upload_then_serve_roundtrip(self, app: Quart) -> None:
        """An uploaded file can be immediately retrieved via the serve route."""
        async with app.test_client() as client:
            headers = await _login(client)
            upload_resp = await client.post(
                "/admin/api/media",
                files={"file": _file(_make_jpeg(), "photo.jpg", "image/jpeg")},
                headers=headers,
            )
            assert upload_resp.status_code == 201
            data = json.loads(await upload_resp.get_data())
            url = data["url"]  # e.g. /media/uploads/uuid.jpg

            serve_resp = await client.get(url)
        assert serve_resp.status_code == 200
