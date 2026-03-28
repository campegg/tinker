"""Integration tests for the ActivityPub inbox endpoint.

Uses a pre-seeded remote actor in the test database (avoiding live HTTP
calls for actor document fetches) and signs test requests with the same
``sign_request`` helper used in production.  Outbound HTTP (Accept{Follow}
delivery) is intercepted with ``unittest.mock.patch``.

Test scenarios
--------------
- ``Follow`` → 202, follower stored, ``Accept{Follow}`` dispatched.
- ``Undo{Follow}`` → 202, follower record removed.
- ``Create{Note}`` from followed actor → 202, timeline item stored.
- ``Like`` on a local note → 202, Like record and notification created.
- Signature missing → 401.
- Signature invalid (wrong key) → 401.
- Actor spoofing (actor != key owner) → 403.
- Wrong username path → 404.
- Rate limit exceeded → 429.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from quart import Quart

from app import create_app
from app.federation.signatures import sign_request
from app.models.follower import Follower
from app.models.following import Following
from app.models.note import Note
from app.models.remote_actor import RemoteActor

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REMOTE_ACTOR_URI = "https://remote.example.com/users/alice"
REMOTE_KEY_ID = f"{REMOTE_ACTOR_URI}#main-key"
REMOTE_INBOX_URL = f"{REMOTE_ACTOR_URI}/inbox"
LOCAL_DOMAIN = "test.example.com"
LOCAL_USERNAME = "testuser"
LOCAL_ACTOR_URI = f"https://{LOCAL_DOMAIN}/{LOCAL_USERNAME}"
INBOX_PATH = f"/{LOCAL_USERNAME}/inbox"
INBOX_URL = f"https://{LOCAL_DOMAIN}{INBOX_PATH}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_keypair() -> tuple[str, str]:
    """Generate a fresh RSA 2048-bit keypair.

    Returns:
        A ``(public_key_pem, private_key_pem)`` tuple.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return public_pem, private_pem


def _signed_headers(body: bytes, private_pem: str, key_id: str = REMOTE_KEY_ID) -> dict[str, str]:
    """Return the HTTP Signature headers for a signed inbox POST.

    Args:
        body: The raw request body bytes.
        private_pem: The PEM-encoded RSA private key to sign with.
        key_id: The ``keyId`` for the Signature header.

    Returns:
        A dict of headers to include in the test request.
    """
    return sign_request(
        method="POST",
        url=INBOX_URL,
        body=body,
        private_key_pem=private_pem,
        key_id=key_id,
    )


def _make_capturing_delivery_client(
    captured: list[dict[str, Any]],
) -> Any:
    """Return a mock HTTP client that records POST calls.

    Args:
        captured: List that will receive dicts of ``{url, content, headers}``.

    Returns:
        A mock client suitable for patching ``get_http_client``.
    """
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    async def _post(url: str, **kwargs: Any) -> Any:
        captured.append(
            {
                "url": url,
                "content": kwargs.get("content", b""),
                "headers": dict(kwargs.get("headers", {})),
            }
        )
        return mock_response

    mock_client = MagicMock()
    mock_client.post = _post
    return mock_client


async def _wait_for_tasks(timeout: float = 1.0) -> None:
    """Pump the event loop until all pending tasks complete or timeout expires.

    Yields control to the event loop in small increments so that
    ``asyncio.create_task`` background tasks (e.g. inbox processing,
    delivery dispatch) have a chance to run before assertions are made.

    Args:
        timeout: Maximum time to wait in seconds.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
        # If only the current task remains, we're done.
        pending = [t for t in asyncio.all_tasks() if not t.done() and t != asyncio.current_task()]
        if not pending:
            break


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def app(tmp_path: Any) -> AsyncGenerator[Quart, None]:
    """Create a test application with a fresh temporary database."""
    import os

    db_path = str(tmp_path / "test.db")
    os.environ["TINKER_DOMAIN"] = LOCAL_DOMAIN
    os.environ["TINKER_DB_PATH"] = db_path
    os.environ["TINKER_MEDIA_PATH"] = str(tmp_path / "media")
    os.environ["TINKER_SECRET_KEY"] = "test-secret-key"
    os.environ["TINKER_USERNAME"] = LOCAL_USERNAME

    application = create_app()

    from sqlalchemy import create_engine

    from app.models.base import Base

    sync_engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    async with application.test_app():
        yield application


@pytest.fixture
def remote_keypair() -> tuple[str, str]:
    """A fresh RSA keypair simulating a remote actor's signing key.

    Returns:
        ``(public_key_pem, private_key_pem)`` for the mock remote actor.
    """
    return _generate_keypair()


@pytest.fixture
def wrong_keypair() -> tuple[str, str]:
    """An unrelated RSA keypair for testing signature rejection.

    Returns:
        ``(public_key_pem, private_key_pem)`` that is *not* registered
        for the remote actor.
    """
    return _generate_keypair()


async def _seed_remote_actor(app: Quart, public_pem: str) -> None:
    """Seed a RemoteActor record in the test database.

    Pre-populates the remote actor cache so that inbox signature
    verification can find the public key without making HTTP calls.

    Args:
        app: The test Quart application.
        public_pem: The PEM-encoded public key to store for the actor.
    """
    session_factory = app.config["DB_SESSION_FACTORY"]
    async with session_factory() as session:
        from app.repositories.remote_actor import RemoteActorRepository

        repo = RemoteActorRepository(session)
        actor = RemoteActor(
            uri=REMOTE_ACTOR_URI,
            display_name="Alice",
            handle="alice@remote.example.com",
            inbox_url=REMOTE_INBOX_URL,
            shared_inbox_url=None,
            public_key=public_pem,
            fetched_at=datetime.now(UTC),
        )
        await repo.add(actor)
        await repo.commit()


async def _seed_following(app: Quart) -> None:
    """Seed a Following record so Create{Note} ends up in the timeline.

    Args:
        app: The test Quart application.
    """
    session_factory = app.config["DB_SESSION_FACTORY"]
    async with session_factory() as session:
        from app.repositories.following import FollowingRepository

        repo = FollowingRepository(session)
        following = Following(
            actor_uri=REMOTE_ACTOR_URI,
            inbox_url=REMOTE_INBOX_URL,
            display_name="Alice",
            status="accepted",
        )
        repo._session.add(following)
        await session.flush()
        await repo.commit()


async def _seed_local_note(app: Quart) -> Note:
    """Seed a local note so that Like/boost notifications can be tested.

    Args:
        app: The test Quart application.

    Returns:
        The created Note instance (with its ``ap_id`` populated).
    """
    session_factory = app.config["DB_SESSION_FACTORY"]
    async with session_factory() as session:
        from app.services.note import NoteService

        svc = NoteService(session, LOCAL_DOMAIN, LOCAL_USERNAME)
        note = await svc.create("Hello world")
        await session.commit()
        return note


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInboxBasicValidation:
    """Tests for path routing and request structure validation."""

    async def test_wrong_username_returns_404(
        self, app: Quart, remote_keypair: tuple[str, str]
    ) -> None:
        """Inbox endpoint returns 404 when the username does not match."""
        _, private_pem = remote_keypair
        body = json.dumps(
            {"type": "Like", "id": "https://r.example.com/like/1", "actor": REMOTE_ACTOR_URI}
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            "/wronguser/inbox",
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 404

    async def test_missing_signature_returns_401(self, app: Quart) -> None:
        """Inbox endpoint returns 401 when no Signature header is present."""
        body = json.dumps(
            {"type": "Like", "id": "https://r.example.com/like/1", "actor": REMOTE_ACTOR_URI}
        ).encode()

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={"Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 401

    async def test_invalid_json_returns_400(
        self, app: Quart, remote_keypair: tuple[str, str]
    ) -> None:
        """Inbox endpoint returns 400 for malformed JSON bodies."""
        _, private_pem = remote_keypair
        body = b"not-valid-json"
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 400

    async def test_activity_missing_fields_returns_400(
        self, app: Quart, remote_keypair: tuple[str, str]
    ) -> None:
        """Inbox endpoint returns 400 when type/id/actor fields are absent."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)

        # Missing 'id' field.
        body = json.dumps({"type": "Like", "actor": REMOTE_ACTOR_URI}).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 400


class TestSignatureVerification:
    """Tests for HTTP Signature verification logic."""

    async def test_invalid_signature_returns_401(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
        wrong_keypair: tuple[str, str],
    ) -> None:
        """Returns 401 when the request is signed with an unknown key."""
        public_pem, _remote_private = remote_keypair
        _, wrong_private = wrong_keypair

        # Seed the actor with the *correct* public key, but sign with wrong key.
        await _seed_remote_actor(app, public_pem)

        body = json.dumps(
            {
                "type": "Like",
                "id": "https://remote.example.com/likes/1",
                "actor": REMOTE_ACTOR_URI,
                "object": f"https://{LOCAL_DOMAIN}/notes/some-uuid",
            }
        ).encode()

        # Sign with the WRONG private key.
        headers = _signed_headers(body, wrong_private)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 401

    async def test_actor_spoofing_returns_403(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """Returns 403 when activity.actor does not match the signing key owner."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)

        # Sign as REMOTE_ACTOR_URI but claim a different actor.
        body = json.dumps(
            {
                "type": "Like",
                "id": "https://remote.example.com/likes/2",
                "actor": "https://attacker.example.com/users/eve",
                "object": f"https://{LOCAL_DOMAIN}/notes/some-uuid",
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 403


class TestFollowActivity:
    """Tests for incoming Follow and Undo{Follow} activities."""

    async def test_follow_creates_follower_and_sends_accept(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """A valid Follow creates a Follower record and dispatches Accept{Follow}."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)

        body = json.dumps(
            {
                "@context": "https://www.w3.org/ns/activitystreams",
                "type": "Follow",
                "id": "https://remote.example.com/follows/1",
                "actor": REMOTE_ACTOR_URI,
                "object": LOCAL_ACTOR_URI,
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        captured: list[dict[str, Any]] = []
        client_cls = _make_capturing_delivery_client(captured)

        client = app.test_client()
        with patch("app.federation.delivery.get_http_client", return_value=client_cls):
            resp = await client.post(
                INBOX_PATH,
                data=body,
                headers={**headers, "Content-Type": "application/activity+json"},
            )
            assert resp.status_code == 202

            # Wait for the background task (inbox processing + delivery).
            await _wait_for_tasks()

        # Follower record should be stored.
        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as session:
            from app.repositories.follower import FollowerRepository

            repo = FollowerRepository(session)
            follower = await repo.get_by_actor_uri(REMOTE_ACTOR_URI)

        assert follower is not None
        assert follower.status == "accepted"
        assert follower.inbox_url == REMOTE_INBOX_URL

        # Accept{Follow} should have been delivered.
        assert len(captured) == 1
        accept_payload = json.loads(captured[0]["content"])
        assert accept_payload["type"] == "Accept"
        assert accept_payload["actor"] == LOCAL_ACTOR_URI
        assert captured[0]["url"] == REMOTE_INBOX_URL

    async def test_follow_creates_notification(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """A valid Follow creates a 'follow' notification record."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)

        body = json.dumps(
            {
                "type": "Follow",
                "id": "https://remote.example.com/follows/2",
                "actor": REMOTE_ACTOR_URI,
                "object": LOCAL_ACTOR_URI,
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        null_delivery_cls = _make_capturing_delivery_client([])
        with patch("app.federation.delivery.get_http_client", return_value=null_delivery_cls):
            resp = await client.post(
                INBOX_PATH,
                data=body,
                headers={**headers, "Content-Type": "application/activity+json"},
            )
            assert resp.status_code == 202
            await _wait_for_tasks()

        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as session:
            from app.repositories.notification import NotificationRepository

            repo = NotificationRepository(session)
            notifications = await repo.get_recent()

        assert len(notifications) == 1
        assert notifications[0].type == "follow"
        assert notifications[0].actor_uri == REMOTE_ACTOR_URI

    async def test_undo_follow_removes_follower(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """An Undo{Follow} removes the corresponding Follower record."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)

        # Seed a follower directly.
        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as session:
            from app.repositories.follower import FollowerRepository

            repo = FollowerRepository(session)
            follower = Follower(
                actor_uri=REMOTE_ACTOR_URI,
                inbox_url=REMOTE_INBOX_URL,
                status="accepted",
            )
            repo._session.add(follower)
            await session.flush()
            await repo.commit()

        body = json.dumps(
            {
                "type": "Undo",
                "id": "https://remote.example.com/undos/1",
                "actor": REMOTE_ACTOR_URI,
                "object": {
                    "type": "Follow",
                    "id": "https://remote.example.com/follows/1",
                    "actor": REMOTE_ACTOR_URI,
                    "object": LOCAL_ACTOR_URI,
                },
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 202
        await _wait_for_tasks()

        async with session_factory() as session:
            from app.repositories.follower import FollowerRepository

            repo = FollowerRepository(session)
            remaining = await repo.get_by_actor_uri(REMOTE_ACTOR_URI)

        assert remaining is None


class TestCreateNoteActivity:
    """Tests for incoming Create{Note} activities."""

    async def test_create_note_from_followed_actor_adds_timeline_item(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """A Create{Note} from a followed actor is stored in the timeline."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)
        await _seed_following(app)

        note_uri = f"{REMOTE_ACTOR_URI}/posts/1"
        body = json.dumps(
            {
                "type": "Create",
                "id": f"{REMOTE_ACTOR_URI}/activities/1",
                "actor": REMOTE_ACTOR_URI,
                "object": {
                    "type": "Note",
                    "id": note_uri,
                    "attributedTo": REMOTE_ACTOR_URI,
                    "content": "<p>Hello from the fediverse!</p>",
                    "to": ["https://www.w3.org/ns/activitystreams#Public"],
                },
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 202
        await _wait_for_tasks()

        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as session:
            from app.repositories.timeline_item import TimelineItemRepository

            repo = TimelineItemRepository(session)
            item = await repo.get_by_object_uri(note_uri)

        assert item is not None
        assert item.activity_type == "Create"
        assert item.actor_uri == REMOTE_ACTOR_URI
        # Content should be sanitised HTML.
        assert item.content_html is not None
        assert "<p>" in item.content_html

    async def test_create_note_from_unfollowed_actor_not_in_timeline(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """A Create{Note} from an actor not followed is NOT stored in timeline."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)
        # No _seed_following call — we don't follow this actor.

        note_uri = f"{REMOTE_ACTOR_URI}/posts/2"
        body = json.dumps(
            {
                "type": "Create",
                "id": f"{REMOTE_ACTOR_URI}/activities/2",
                "actor": REMOTE_ACTOR_URI,
                "object": {
                    "type": "Note",
                    "id": note_uri,
                    "attributedTo": REMOTE_ACTOR_URI,
                    "content": "<p>You should not see this.</p>",
                },
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 202
        await _wait_for_tasks()

        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as session:
            from app.repositories.timeline_item import TimelineItemRepository

            repo = TimelineItemRepository(session)
            item = await repo.get_by_object_uri(note_uri)

        assert item is None

    async def test_create_note_reply_to_local_note_creates_notification(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """A reply to a local note creates a 'reply' notification."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)
        local_note = await _seed_local_note(app)

        reply_uri = f"{REMOTE_ACTOR_URI}/posts/3"
        body = json.dumps(
            {
                "type": "Create",
                "id": f"{REMOTE_ACTOR_URI}/activities/3",
                "actor": REMOTE_ACTOR_URI,
                "object": {
                    "type": "Note",
                    "id": reply_uri,
                    "attributedTo": REMOTE_ACTOR_URI,
                    "content": "<p>@you Great post!</p>",
                    "inReplyTo": local_note.ap_id,
                },
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 202
        await _wait_for_tasks()

        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as session:
            from app.repositories.notification import NotificationRepository

            repo = NotificationRepository(session)
            notifications = await repo.get_recent()

        assert len(notifications) == 1
        assert notifications[0].type == "reply"
        assert notifications[0].actor_uri == REMOTE_ACTOR_URI
        assert notifications[0].object_uri == reply_uri

    async def test_create_note_xss_content_is_sanitised(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """HTML in Create{Note} is sanitised before storage — XSS stripped."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)
        await _seed_following(app)

        note_uri = f"{REMOTE_ACTOR_URI}/posts/4"
        malicious_content = '<p>Hello</p><script>alert("xss")</script><p>World</p>'
        body = json.dumps(
            {
                "type": "Create",
                "id": f"{REMOTE_ACTOR_URI}/activities/4",
                "actor": REMOTE_ACTOR_URI,
                "object": {
                    "type": "Note",
                    "id": note_uri,
                    "attributedTo": REMOTE_ACTOR_URI,
                    "content": malicious_content,
                },
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 202
        await _wait_for_tasks()

        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as session:
            from app.repositories.timeline_item import TimelineItemRepository

            repo = TimelineItemRepository(session)
            item = await repo.get_by_object_uri(note_uri)

        assert item is not None
        assert item.content_html is not None
        assert "<script>" not in item.content_html
        assert "alert" not in item.content_html
        assert "<p>Hello</p>" in item.content_html


class TestLikeActivity:
    """Tests for incoming Like and Undo{Like} activities."""

    async def test_like_on_local_note_creates_notification(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """A Like on a local note creates a Like record and notification."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)
        local_note = await _seed_local_note(app)

        like_id = "https://remote.example.com/likes/1"
        body = json.dumps(
            {
                "type": "Like",
                "id": like_id,
                "actor": REMOTE_ACTOR_URI,
                "object": local_note.ap_id,
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 202
        await _wait_for_tasks()

        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as session:
            from app.repositories.like import LikeRepository
            from app.repositories.notification import NotificationRepository

            like_repo = LikeRepository(session)
            like = await like_repo.get_by_activity_uri(like_id)

            notif_repo = NotificationRepository(session)
            notifications = await notif_repo.get_recent()

        assert like is not None
        assert like.note_uri == local_note.ap_id
        assert like.actor_uri == REMOTE_ACTOR_URI

        assert len(notifications) == 1
        assert notifications[0].type == "like"
        assert notifications[0].actor_uri == REMOTE_ACTOR_URI

    async def test_like_is_idempotent(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """Duplicate Like activities are ignored (idempotent processing)."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)
        local_note = await _seed_local_note(app)

        like_id = "https://remote.example.com/likes/2"
        body = json.dumps(
            {
                "type": "Like",
                "id": like_id,
                "actor": REMOTE_ACTOR_URI,
                "object": local_note.ap_id,
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        # Send the same Like activity twice.
        for _ in range(2):
            resp = await client.post(
                INBOX_PATH,
                data=body,
                headers={**headers, "Content-Type": "application/activity+json"},
            )
            assert resp.status_code == 202
            await _wait_for_tasks()

        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as session:
            from app.repositories.like import LikeRepository

            repo = LikeRepository(session)
            all_likes = await repo.get_all()

        # Only one Like record despite two deliveries.
        assert len(all_likes) == 1

    async def test_undo_like_removes_record(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """Undo{Like} removes the corresponding Like record."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)
        local_note = await _seed_local_note(app)

        like_id = "https://remote.example.com/likes/3"
        # First, store the Like directly.
        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as session:
            from app.models.like import Like
            from app.repositories.like import LikeRepository

            repo = LikeRepository(session)
            like = Like(
                note_uri=local_note.ap_id,
                actor_uri=REMOTE_ACTOR_URI,
                activity_uri=like_id,
            )
            await repo.add(like)
            await repo.commit()

        # Now send the Undo{Like}.
        body = json.dumps(
            {
                "type": "Undo",
                "id": "https://remote.example.com/undos/like/1",
                "actor": REMOTE_ACTOR_URI,
                "object": {
                    "type": "Like",
                    "id": like_id,
                    "actor": REMOTE_ACTOR_URI,
                    "object": local_note.ap_id,
                },
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 202
        await _wait_for_tasks()

        async with session_factory() as session:
            from app.repositories.like import LikeRepository

            repo = LikeRepository(session)
            remaining = await repo.get_by_activity_uri(like_id)

        assert remaining is None


class TestAcceptRejectFollow:
    """Tests for Accept{Follow} and Reject{Follow} from remote actors."""

    async def test_accept_follow_marks_following_accepted(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """Accept{Follow} updates the local Following record to 'accepted'."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)

        # Seed a pending Following record.
        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as session:
            following = Following(
                actor_uri=REMOTE_ACTOR_URI,
                inbox_url=REMOTE_INBOX_URL,
                status="pending",
            )
            session.add(following)
            await session.flush()
            await session.commit()

        body = json.dumps(
            {
                "type": "Accept",
                "id": f"{REMOTE_ACTOR_URI}#accepts/follows/1",
                "actor": REMOTE_ACTOR_URI,
                "object": {
                    "type": "Follow",
                    "id": f"{LOCAL_ACTOR_URI}/follows/1",
                    "actor": LOCAL_ACTOR_URI,
                    "object": REMOTE_ACTOR_URI,
                },
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 202
        await _wait_for_tasks()

        async with session_factory() as session:
            from app.repositories.following import FollowingRepository

            repo = FollowingRepository(session)
            updated = await repo.get_by_actor_uri(REMOTE_ACTOR_URI)

        assert updated is not None
        assert updated.status == "accepted"

    async def test_reject_follow_removes_following_record(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """Reject{Follow} removes the corresponding Following record."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)

        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as session:
            following = Following(
                actor_uri=REMOTE_ACTOR_URI,
                inbox_url=REMOTE_INBOX_URL,
                status="pending",
            )
            session.add(following)
            await session.flush()
            await session.commit()

        body = json.dumps(
            {
                "type": "Reject",
                "id": f"{REMOTE_ACTOR_URI}#rejects/follows/1",
                "actor": REMOTE_ACTOR_URI,
                "object": {
                    "type": "Follow",
                    "id": f"{LOCAL_ACTOR_URI}/follows/1",
                    "actor": LOCAL_ACTOR_URI,
                    "object": REMOTE_ACTOR_URI,
                },
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 202
        await _wait_for_tasks()

        async with session_factory() as session:
            from app.repositories.following import FollowingRepository

            repo = FollowingRepository(session)
            remaining = await repo.get_by_actor_uri(REMOTE_ACTOR_URI)

        assert remaining is None


class TestAcceptFollowActivityShape:
    """Tests verifying the shape of the Accept{Follow} activity sent back to followers.

    Rather than intercepting HTTP (which races with the background delivery
    task), these tests read the serialised activity directly from the
    ``DeliveryQueue`` table, which is written synchronously during inbox
    processing before any network I/O occurs.
    """

    async def _send_follow_and_get_accept_payload(
        self,
        app: Quart,
        public_pem: str,
        private_pem: str,
        follow_id: str,
    ) -> dict[str, Any]:
        """Helper: send a Follow, wait for processing, return queued Accept payload.

        Args:
            app: The test Quart application.
            public_pem: The remote actor's public key (stored in the DB cache).
            private_pem: The remote actor's private key (used to sign the Follow).
            follow_id: A unique AP URI for the Follow activity.

        Returns:
            The parsed JSON dict of the first ``Accept`` activity found in the
            ``DeliveryQueue`` table.
        """
        await _seed_remote_actor(app, public_pem)

        body = json.dumps(
            {
                "type": "Follow",
                "id": follow_id,
                "actor": REMOTE_ACTOR_URI,
                "object": LOCAL_ACTOR_URI,
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        null_delivery = _make_capturing_delivery_client([])
        with patch("app.federation.delivery.get_http_client", return_value=null_delivery):
            client = app.test_client()
            resp = await client.post(
                INBOX_PATH,
                data=body,
                headers={**headers, "Content-Type": "application/activity+json"},
            )
            assert resp.status_code == 202
            await _wait_for_tasks()

        # Read the queued activity payload from the DB — it is persisted
        # synchronously before any delivery attempt, so it is always present
        # regardless of delivery task timing.
        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as session:
            from app.repositories.delivery_queue import DeliveryQueueRepository

            repo = DeliveryQueueRepository(session)
            items = await repo.get_pending()
            # get_pending may be empty if already delivered; fall back to all recent.
            if not items:
                from sqlalchemy import select

                from app.models.delivery_queue import DeliveryQueue

                result = await session.execute(
                    select(DeliveryQueue).order_by(DeliveryQueue.created_at.desc()).limit(10)
                )
                items = list(result.scalars().all())

        assert items, "No DeliveryQueue entries found — Follow processing may have failed"

        accept_items: list[dict[str, Any]] = [
            dict[str, Any](json.loads(item.activity_json))
            for item in items
            if json.loads(item.activity_json).get("type") == "Accept"
        ]
        assert accept_items, (
            f"No Accept activity found in DeliveryQueue. "
            f"Found types: {[json.loads(i.activity_json).get('type') for i in items]}"
        )
        return accept_items[0]

    async def test_accept_follow_uses_array_context(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """Accept{Follow} must use a two-element @context array, not a plain string.

        A single-string ``"https://www.w3.org/ns/activitystreams"`` context strips
        the security vocabulary and may cause Mastodon to reject or misparse the
        Accept activity.  The canonical form used by every other activity in the
        codebase is ``["https://www.w3.org/ns/activitystreams",
        "https://w3id.org/security/v1"]``.
        """
        public_pem, private_pem = remote_keypair
        accept_payload = await self._send_follow_and_get_accept_payload(
            app,
            public_pem,
            private_pem,
            follow_id=f"{REMOTE_ACTOR_URI}/activities/follow/ctx-test",
        )

        assert accept_payload["type"] == "Accept"
        ctx = accept_payload.get("@context")
        assert isinstance(ctx, list), (
            f"Accept{{Follow}} @context must be a list, got {type(ctx).__name__}: {ctx!r}"
        )
        assert "https://www.w3.org/ns/activitystreams" in ctx
        assert "https://w3id.org/security/v1" in ctx

    async def test_accept_follow_context_is_not_plain_string(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """Accept{Follow} @context must not be a plain string (regression guard)."""
        public_pem, private_pem = remote_keypair
        accept_payload = await self._send_follow_and_get_accept_payload(
            app,
            public_pem,
            private_pem,
            follow_id=f"{REMOTE_ACTOR_URI}/activities/follow/str-ctx-test",
        )

        ctx = accept_payload.get("@context")
        assert not isinstance(ctx, str), (
            "Accept{Follow} @context must not be a plain string — "
            "Mastodon requires the array form including the security vocab."
        )


class TestDeleteFromGoneActor:
    """Tests for graceful handling of Delete activities from removed/gone actors.

    Per the ActivityPub spec and Mastodon behaviour, Delete activities sent
    by actors whose profile has been removed will fail signature verification
    because the public key can no longer be fetched.  The inbox must accept
    and discard these (202) rather than returning 401, which would trigger
    unnecessary retry storms from the remote server.
    """

    async def test_delete_from_gone_actor_returns_202(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """Delete from an actor with no cached key and unfetchable document → 202."""
        _, private_pem = remote_keypair
        # Deliberately do NOT seed the remote actor — no cached public key.

        gone_actor_uri = "https://gone.example.com/users/deleted"
        gone_key_id = f"{gone_actor_uri}#main-key"
        gone_inbox_full = f"https://{LOCAL_DOMAIN}{INBOX_PATH}"

        body = json.dumps(
            {
                "type": "Delete",
                "id": f"{gone_actor_uri}/activities/delete/1",
                "actor": gone_actor_uri,
                "object": {
                    "id": f"{gone_actor_uri}/posts/1",
                    "type": "Tombstone",
                },
            }
        ).encode()

        sig_headers = sign_request(
            method="POST",
            url=gone_inbox_full,
            body=body,
            private_key_pem=private_pem,
            key_id=gone_key_id,
        )

        # Both the initial get_public_key lookup and the refresh attempt return
        # None — simulates the actor being permanently gone/unreachable.
        with (
            patch(
                "app.services.remote_actor.RemoteActorService.get_public_key",
                AsyncMock(return_value=None),
            ),
            patch(
                "app.services.remote_actor.RemoteActorService.refresh",
                AsyncMock(return_value=None),
            ),
        ):
            client = app.test_client()
            resp = await client.post(
                INBOX_PATH,
                data=body,
                headers={**sig_headers, "Content-Type": "application/activity+json"},
            )

        assert resp.status_code == 202, (
            f"Expected 202 for Delete from gone actor, got {resp.status_code}. "
            "Delete from a removed actor must be discarded gracefully, not rejected."
        )

    async def test_delete_with_invalid_sig_from_known_actor_returns_401(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
        wrong_keypair: tuple[str, str],
    ) -> None:
        """Delete signed with wrong key for a known actor still returns 401.

        Only an *unfetchable* actor (gone entirely) gets the graceful 202.
        A known actor signing with the wrong key is a genuine security failure
        and must be rejected.
        """
        public_pem, _correct_private_pem = remote_keypair
        _, wrong_private_pem = wrong_keypair
        await _seed_remote_actor(app, public_pem)

        body = json.dumps(
            {
                "type": "Delete",
                "id": f"{REMOTE_ACTOR_URI}/activities/delete/bad-sig",
                "actor": REMOTE_ACTOR_URI,
                "object": {
                    "id": f"{REMOTE_ACTOR_URI}/posts/99",
                    "type": "Tombstone",
                },
            }
        ).encode()

        # Sign with the *wrong* key so verification fails, but the actor IS known.
        headers = _signed_headers(body, wrong_private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 401, (
            f"Expected 401 for Delete with invalid signature from known actor, "
            f"got {resp.status_code}."
        )

    async def test_non_delete_from_gone_actor_returns_401(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """Non-Delete activity from unfetchable actor still returns 401.

        The graceful 202 is reserved for Delete activities specifically —
        other activity types from unverifiable actors must be rejected.
        """
        _, private_pem = remote_keypair
        # Do NOT seed the remote actor.

        gone_actor_uri = "https://gone.example.com/users/mystery"
        gone_key_id = f"{gone_actor_uri}#main-key"
        gone_inbox_full = f"https://{LOCAL_DOMAIN}{INBOX_PATH}"

        body = json.dumps(
            {
                "type": "Follow",
                "id": f"{gone_actor_uri}/activities/follow/1",
                "actor": gone_actor_uri,
                "object": LOCAL_ACTOR_URI,
            }
        ).encode()

        sig_headers = sign_request(
            method="POST",
            url=gone_inbox_full,
            body=body,
            private_key_pem=private_pem,
            key_id=gone_key_id,
        )

        with (
            patch(
                "app.services.remote_actor.RemoteActorService.get_public_key",
                AsyncMock(return_value=None),
            ),
            patch(
                "app.services.remote_actor.RemoteActorService.refresh",
                AsyncMock(return_value=None),
            ),
        ):
            client = app.test_client()
            resp = await client.post(
                INBOX_PATH,
                data=body,
                headers={**sig_headers, "Content-Type": "application/activity+json"},
            )

        assert resp.status_code == 401, (
            f"Expected 401 for non-Delete from unverifiable actor, got {resp.status_code}."
        )


class TestRateLimit:
    """Tests for the inbox per-IP rate limit."""

    async def test_rate_limit_exceeded_returns_429(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """Exceeding the per-IP inbox rate limit returns 429."""
        from app.federation import inbox as inbox_module

        # Reset rate limit state for this test.
        async with inbox_module._rate_limit_lock:
            inbox_module._inbox_attempts.clear()

        original_max = inbox_module._INBOX_RATE_LIMIT_MAX
        # Temporarily lower the limit to 2 for the test.
        inbox_module._INBOX_RATE_LIMIT_MAX = 2

        try:
            public_pem, private_pem = remote_keypair
            await _seed_remote_actor(app, public_pem)

            body = json.dumps(
                {
                    "type": "Like",
                    "id": "https://remote.example.com/likes/rl",
                    "actor": REMOTE_ACTOR_URI,
                    "object": "https://example.com/notes/foo",
                }
            ).encode()
            headers = _signed_headers(body, private_pem)

            client = app.test_client()
            responses = []
            for _ in range(3):
                resp = await client.post(
                    INBOX_PATH,
                    data=body,
                    headers={**headers, "Content-Type": "application/activity+json"},
                )
                responses.append(resp.status_code)

            # First two succeed; third exceeds the limit.
            assert 429 in responses

        finally:
            inbox_module._INBOX_RATE_LIMIT_MAX = original_max
            async with inbox_module._rate_limit_lock:
                inbox_module._inbox_attempts.clear()


class TestAnnounceActivity:
    """Tests for incoming Announce (boost) activities."""

    async def test_announce_from_followed_actor_creates_timeline_item(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """An Announce from a followed actor creates a timeline item."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)
        await _seed_following(app)

        boosted_note_uri = "https://other.example.com/posts/99"
        body = json.dumps(
            {
                "type": "Announce",
                "id": f"{REMOTE_ACTOR_URI}/activities/announce/1",
                "actor": REMOTE_ACTOR_URI,
                "object": {
                    "type": "Note",
                    "id": boosted_note_uri,
                    "attributedTo": "https://other.example.com/users/bob",
                    "content": "<p>Boosted content from Bob</p>",
                },
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 202
        await _wait_for_tasks()

        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as session:
            from app.repositories.timeline_item import TimelineItemRepository

            repo = TimelineItemRepository(session)
            item = await repo.get_by_actor_type_and_object_uri(
                actor_uri=REMOTE_ACTOR_URI,
                activity_type="Announce",
                original_object_uri=boosted_note_uri,
            )

        assert item is not None
        assert item.activity_type == "Announce"
        assert item.actor_uri == REMOTE_ACTOR_URI
        assert item.original_object_uri == boosted_note_uri
        assert item.content_html is not None
        assert "Boosted content" in item.content_html

    async def test_announce_from_unfollowed_actor_ignored(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """An Announce from an actor we don't follow creates no timeline item."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)
        # No _seed_following — we do not follow this actor.

        boosted_note_uri = "https://other.example.com/posts/unfollowed"
        body = json.dumps(
            {
                "type": "Announce",
                "id": f"{REMOTE_ACTOR_URI}/activities/announce/2",
                "actor": REMOTE_ACTOR_URI,
                "object": boosted_note_uri,
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 202
        await _wait_for_tasks()

        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as session:
            from app.repositories.timeline_item import TimelineItemRepository

            repo = TimelineItemRepository(session)
            item = await repo.get_by_actor_type_and_object_uri(
                actor_uri=REMOTE_ACTOR_URI,
                activity_type="Announce",
                original_object_uri=boosted_note_uri,
            )

        assert item is None

    async def test_announce_of_local_note_creates_notification(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """An Announce of a local note creates a 'boost' notification."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)
        local_note = await _seed_local_note(app)

        body = json.dumps(
            {
                "type": "Announce",
                "id": f"{REMOTE_ACTOR_URI}/activities/announce/3",
                "actor": REMOTE_ACTOR_URI,
                "object": local_note.ap_id,
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 202
        await _wait_for_tasks()

        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as session:
            from app.repositories.notification import NotificationRepository

            repo = NotificationRepository(session)
            notifications = await repo.get_recent()

        assert len(notifications) == 1
        assert notifications[0].type == "boost"
        assert notifications[0].actor_uri == REMOTE_ACTOR_URI
        assert notifications[0].object_uri == local_note.ap_id

    async def test_duplicate_announce_is_idempotent(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """Duplicate Announce activities produce only one timeline item."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)
        await _seed_following(app)

        boosted_note_uri = "https://other.example.com/posts/dup"
        body = json.dumps(
            {
                "type": "Announce",
                "id": f"{REMOTE_ACTOR_URI}/activities/announce/4",
                "actor": REMOTE_ACTOR_URI,
                "object": {
                    "type": "Note",
                    "id": boosted_note_uri,
                    "attributedTo": "https://other.example.com/users/bob",
                    "content": "<p>Duplicate boost test</p>",
                },
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        # Send the same Announce activity twice.
        for _ in range(2):
            resp = await client.post(
                INBOX_PATH,
                data=body,
                headers={**headers, "Content-Type": "application/activity+json"},
            )
            assert resp.status_code == 202
            await _wait_for_tasks()

        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as session:
            from app.repositories.timeline_item import TimelineItemRepository

            repo = TimelineItemRepository(session)
            all_items = await repo.get_recent(limit=50)

        announce_items = [
            i
            for i in all_items
            if i.activity_type == "Announce" and i.original_object_uri == boosted_note_uri
        ]
        assert len(announce_items) == 1


class TestUndoAnnounceActivity:
    """Tests for incoming Undo{Announce} activities."""

    async def test_undo_announce_removes_timeline_item(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """Undo{Announce} removes the corresponding timeline item."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)

        boosted_note_uri = "https://other.example.com/posts/to-unboost"

        # Seed a timeline item for the Announce directly.
        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as session:
            from app.models.timeline_item import TimelineItem
            from app.repositories.timeline_item import TimelineItemRepository

            repo = TimelineItemRepository(session)
            item = TimelineItem(
                activity_type="Announce",
                actor_uri=REMOTE_ACTOR_URI,
                actor_name="Alice",
                content_html="<p>Previously boosted</p>",
                original_object_uri=boosted_note_uri,
            )
            await repo.add(item)
            await repo.commit()

        # Verify the item exists before we undo.
        async with session_factory() as session:
            from app.repositories.timeline_item import TimelineItemRepository

            repo = TimelineItemRepository(session)
            existing = await repo.get_by_actor_type_and_object_uri(
                actor_uri=REMOTE_ACTOR_URI,
                activity_type="Announce",
                original_object_uri=boosted_note_uri,
            )
        assert existing is not None

        # Now send the Undo{Announce}.
        body = json.dumps(
            {
                "type": "Undo",
                "id": f"{REMOTE_ACTOR_URI}/undos/announce/1",
                "actor": REMOTE_ACTOR_URI,
                "object": {
                    "type": "Announce",
                    "id": f"{REMOTE_ACTOR_URI}/activities/announce/1",
                    "actor": REMOTE_ACTOR_URI,
                    "object": boosted_note_uri,
                },
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 202
        await _wait_for_tasks()

        async with session_factory() as session:
            from app.repositories.timeline_item import TimelineItemRepository

            repo = TimelineItemRepository(session)
            remaining = await repo.get_by_actor_type_and_object_uri(
                actor_uri=REMOTE_ACTOR_URI,
                activity_type="Announce",
                original_object_uri=boosted_note_uri,
            )

        assert remaining is None

    async def test_undo_announce_no_matching_item_is_noop(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """Undo{Announce} for a nonexistent timeline item completes without error."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)

        body = json.dumps(
            {
                "type": "Undo",
                "id": f"{REMOTE_ACTOR_URI}/undos/announce/ghost",
                "actor": REMOTE_ACTOR_URI,
                "object": {
                    "type": "Announce",
                    "id": f"{REMOTE_ACTOR_URI}/activities/announce/ghost",
                    "actor": REMOTE_ACTOR_URI,
                    "object": "https://other.example.com/posts/nonexistent",
                },
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 202
        await _wait_for_tasks()

        # No assertion on DB state — the point is that it did not error.


class TestUpdateActivity:
    """Tests for incoming Update activities."""

    async def test_update_overwrites_timeline_content(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """An Update activity overwrites the cached timeline content."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)

        note_uri = f"{REMOTE_ACTOR_URI}/posts/updatable"

        # Seed a timeline item with the original content.
        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as session:
            from app.models.timeline_item import TimelineItem
            from app.repositories.timeline_item import TimelineItemRepository

            repo = TimelineItemRepository(session)
            item = TimelineItem(
                activity_type="Create",
                actor_uri=REMOTE_ACTOR_URI,
                actor_name="Alice",
                content_html="<p>Original content</p>",
                original_object_uri=note_uri,
            )
            await repo.add(item)
            await repo.commit()

        # Send an Update activity with new content.
        body = json.dumps(
            {
                "type": "Update",
                "id": f"{REMOTE_ACTOR_URI}/activities/update/1",
                "actor": REMOTE_ACTOR_URI,
                "object": {
                    "type": "Note",
                    "id": note_uri,
                    "attributedTo": REMOTE_ACTOR_URI,
                    "content": "<p>Updated content after edit</p>",
                },
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 202
        await _wait_for_tasks()

        async with session_factory() as session:
            from app.repositories.timeline_item import TimelineItemRepository

            repo = TimelineItemRepository(session)
            result = await repo.get_by_object_uri(note_uri)
            assert result is not None
            assert result.content_html is not None
            assert "Updated content after edit" in result.content_html
            assert "Original content" not in result.content_html

    async def test_update_for_unknown_object_is_noop(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """An Update for an object not in the timeline completes without error."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)

        body = json.dumps(
            {
                "type": "Update",
                "id": f"{REMOTE_ACTOR_URI}/activities/update/ghost",
                "actor": REMOTE_ACTOR_URI,
                "object": {
                    "type": "Note",
                    "id": f"{REMOTE_ACTOR_URI}/posts/nonexistent",
                    "attributedTo": REMOTE_ACTOR_URI,
                    "content": "<p>This targets nothing</p>",
                },
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 202
        await _wait_for_tasks()

        # No assertion on DB state — the point is that it did not error.

    async def test_update_sanitizes_html_content(
        self,
        app: Quart,
        remote_keypair: tuple[str, str],
    ) -> None:
        """An Update with a <script> tag has the tag stripped before storage."""
        public_pem, private_pem = remote_keypair
        await _seed_remote_actor(app, public_pem)

        note_uri = f"{REMOTE_ACTOR_URI}/posts/xss-update"

        # Seed a timeline item to be updated.
        session_factory = app.config["DB_SESSION_FACTORY"]
        async with session_factory() as session:
            from app.models.timeline_item import TimelineItem
            from app.repositories.timeline_item import TimelineItemRepository

            repo = TimelineItemRepository(session)
            item = TimelineItem(
                activity_type="Create",
                actor_uri=REMOTE_ACTOR_URI,
                actor_name="Alice",
                content_html="<p>Safe original</p>",
                original_object_uri=note_uri,
            )
            await repo.add(item)
            await repo.commit()

        # Send an Update with malicious HTML.
        malicious_content = '<p>Looks fine</p><script>alert("xss")</script><p>Really</p>'
        body = json.dumps(
            {
                "type": "Update",
                "id": f"{REMOTE_ACTOR_URI}/activities/update/xss",
                "actor": REMOTE_ACTOR_URI,
                "object": {
                    "type": "Note",
                    "id": note_uri,
                    "attributedTo": REMOTE_ACTOR_URI,
                    "content": malicious_content,
                },
            }
        ).encode()
        headers = _signed_headers(body, private_pem)

        client = app.test_client()
        resp = await client.post(
            INBOX_PATH,
            data=body,
            headers={**headers, "Content-Type": "application/activity+json"},
        )
        assert resp.status_code == 202
        await _wait_for_tasks()

        async with session_factory() as session:
            from app.repositories.timeline_item import TimelineItemRepository

            repo = TimelineItemRepository(session)
            result = await repo.get_by_object_uri(note_uri)
            assert result is not None
            assert result.content_html is not None
            assert "<script>" not in result.content_html
            assert "alert" not in result.content_html
            assert "<p>Looks fine</p>" in result.content_html
            assert "<p>Really</p>" in result.content_html
