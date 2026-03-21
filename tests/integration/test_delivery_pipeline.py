"""Integration tests for the ActivityPub delivery pipeline.

Uses ``unittest.mock.patch`` to intercept outbound ``httpx`` calls,
allowing full verification of HTTP Signature headers and activity payloads
without requiring a real remote server.

Test scenarios:
- Publish a note → Create{Note} POSTed with valid HTTP Signature.
- Edit a note → Update{Note} delivered.
- Delete a note → Delete+Tombstone delivered.
- Shared-inbox deduplication: two followers on the same server → one delivery.
- Retry: delivery fails (500), then succeeds after backoff.
- Crash recovery: pending items re-dispatched on app startup.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from quart import Quart

from app import create_app
from app.federation.delivery import (
    DeliveryService,
    _retry_loop_step,
    dispatch_new_items,
    startup_recovery,
)
from app.federation.outbox import (
    build_create_activity,
    build_delete_activity,
    build_update_activity,
)
from app.federation.signatures import verify_signature
from app.models.follower import Follower
from app.services.keypair import KeypairService
from app.services.note import NoteService

MOCK_INBOX_URL = "https://remote.example.com/inbox"


# ---------------------------------------------------------------------------
# Captured-request helper
# ---------------------------------------------------------------------------


def _make_capturing_client(
    captured: list[dict[str, Any]],
    status_code: int = 202,
) -> Any:
    """Build a mock httpx.AsyncClient that records POST calls.

    Each call to ``client.post(url, content=..., headers=...)`` appends a
    dict ``{"url": url, "content": bytes, "headers": dict}`` to
    ``captured``.

    Args:
        captured: The list to append recorded calls to.
        status_code: HTTP status code to simulate (default 202).

    Returns:
        A mock object suitable for patching ``httpx.AsyncClient``.
    """
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx

        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=MagicMock(status_code=status_code, text="Error"),
        )

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
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    # The constructor returns the mock_client.
    client_cls = MagicMock(return_value=mock_client)
    return client_cls


def _verify_captured_signature(
    captured_call: dict[str, Any],
    public_key_pem: str,
) -> bool:
    """Verify the HTTP Signature on a captured delivery call."""
    from urllib.parse import urlparse

    parsed = urlparse(captured_call["url"])
    return verify_signature(
        method="POST",
        path=parsed.path,
        headers=captured_call["headers"],
        body=captured_call["content"],
        public_key_pem=public_key_pem,
    )


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def app(tmp_path: Any) -> AsyncGenerator[Quart, None]:
    """Create a test application with a real temporary database."""
    import os

    os.environ["TINKER_DOMAIN"] = "local.example.com"
    os.environ["TINKER_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["TINKER_MEDIA_PATH"] = str(tmp_path / "media")
    os.environ["TINKER_SECRET_KEY"] = "test-secret-key-delivery"
    os.environ["TINKER_USERNAME"] = "testuser"

    application = create_app()

    from sqlalchemy import create_engine

    from app.core.database import create_sync_url
    from app.models.base import Base

    sync_engine = create_engine(create_sync_url(application.config["TINKER_DB_PATH"]))
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    async with application.test_app():
        yield application


@pytest.fixture
async def public_key_pem(app: Quart) -> str:
    """Seed the local keypair and return the public key PEM."""
    session_factory = app.config["DB_SESSION_FACTORY"]
    async with session_factory() as session:
        svc = KeypairService(session)
        pub, _ = await svc.get_or_create()
    return pub


@pytest.fixture
async def follower(app: Quart) -> Follower:
    """Seed one accepted follower whose inbox is ``MOCK_INBOX_URL``."""
    session_factory = app.config["DB_SESSION_FACTORY"]
    async with session_factory() as session:
        from app.repositories.follower import FollowerRepository

        repo = FollowerRepository(session)
        f = Follower(
            id=uuid.uuid4(),
            actor_uri="https://remote.example.com/users/alice",
            inbox_url=MOCK_INBOX_URL,
            shared_inbox_url=None,
            display_name="Alice",
            status="accepted",
        )
        await repo.add(f)
        await repo.commit()
    return f


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _delivery_ctx(app: Quart) -> tuple[str, str, str, str]:
    """Return ``(domain, username, private_key_pem, key_id)``."""
    session_factory = app.config["DB_SESSION_FACTORY"]
    domain: str = app.config["TINKER_DOMAIN"]
    username: str = app.config["TINKER_USERNAME"]
    async with session_factory() as session:
        kp = KeypairService(session)
        private_key_pem = await kp.get_private_key()
    key_id = f"https://{domain}/{username}#main-key"
    return domain, username, private_key_pem, key_id


# ---------------------------------------------------------------------------
# Note lifecycle delivery
# ---------------------------------------------------------------------------


class TestNoteDelivery:
    """End-to-end: note operations produce correctly signed deliveries."""

    async def test_create_note_delivered_with_valid_signature(
        self,
        app: Quart,
        follower: Follower,
        public_key_pem: str,
    ) -> None:
        """Create{Note} is delivered with a valid HTTP Signature."""
        captured: list[dict[str, Any]] = []
        client_cls = _make_capturing_client(captured, status_code=202)

        domain, username, private_key_pem, key_id = await _delivery_ctx(app)
        session_factory = app.config["DB_SESSION_FACTORY"]
        semaphore = app.config["DELIVERY_SEMAPHORE"]

        async with session_factory() as session:
            note_svc = NoteService(session, domain, username)
            note = await note_svc.create("Hello, fediverse!")
            actor_uri = f"https://{domain}/{username}"
            activity = build_create_activity(note, actor_uri)
            delivery_svc = DeliveryService(session)
            items = await delivery_svc.fan_out(activity)

        with patch("app.federation.delivery.httpx.AsyncClient", client_cls):
            dispatch_new_items(
                items,
                session_factory=session_factory,
                private_key_pem=private_key_pem,
                key_id=key_id,
                semaphore=semaphore,
            )
            await asyncio.sleep(0.2)

        assert len(captured) == 1
        body = json.loads(captured[0]["content"])
        assert body["type"] == "Create"
        assert body["object"]["type"] == "Note"
        assert _verify_captured_signature(captured[0], public_key_pem)

    async def test_update_note_delivered(
        self,
        app: Quart,
        follower: Follower,
        public_key_pem: str,
    ) -> None:
        """Update{Note} is delivered with a valid HTTP Signature."""
        captured: list[dict[str, Any]] = []
        client_cls = _make_capturing_client(captured, status_code=202)

        domain, username, private_key_pem, key_id = await _delivery_ctx(app)
        session_factory = app.config["DB_SESSION_FACTORY"]
        semaphore = app.config["DELIVERY_SEMAPHORE"]

        async with session_factory() as session:
            note_svc = NoteService(session, domain, username)
            note = await note_svc.create("Original text.")
            note = await note_svc.edit(note, "Edited text.")
            actor_uri = f"https://{domain}/{username}"
            activity = build_update_activity(note, actor_uri)
            delivery_svc = DeliveryService(session)
            items = await delivery_svc.fan_out(activity)

        with patch("app.federation.delivery.httpx.AsyncClient", client_cls):
            dispatch_new_items(
                items,
                session_factory=session_factory,
                private_key_pem=private_key_pem,
                key_id=key_id,
                semaphore=semaphore,
            )
            await asyncio.sleep(0.2)

        assert len(captured) == 1
        body = json.loads(captured[0]["content"])
        assert body["type"] == "Update"
        assert _verify_captured_signature(captured[0], public_key_pem)

    async def test_delete_note_delivers_tombstone(
        self,
        app: Quart,
        follower: Follower,
        public_key_pem: str,
    ) -> None:
        """Delete+Tombstone is delivered with a valid HTTP Signature."""
        captured: list[dict[str, Any]] = []
        client_cls = _make_capturing_client(captured, status_code=202)

        domain, username, private_key_pem, key_id = await _delivery_ctx(app)
        session_factory = app.config["DB_SESSION_FACTORY"]
        semaphore = app.config["DELIVERY_SEMAPHORE"]

        async with session_factory() as session:
            note_svc = NoteService(session, domain, username)
            note = await note_svc.create("Going to delete this.")
            note_ap_id = note.ap_id
            await note_svc.delete(note)
            actor_uri = f"https://{domain}/{username}"
            activity = build_delete_activity(note_ap_id, actor_uri)
            delivery_svc = DeliveryService(session)
            items = await delivery_svc.fan_out(activity)

        with patch("app.federation.delivery.httpx.AsyncClient", client_cls):
            dispatch_new_items(
                items,
                session_factory=session_factory,
                private_key_pem=private_key_pem,
                key_id=key_id,
                semaphore=semaphore,
            )
            await asyncio.sleep(0.2)

        assert len(captured) == 1
        body = json.loads(captured[0]["content"])
        assert body["type"] == "Delete"
        assert body["object"]["type"] == "Tombstone"
        assert _verify_captured_signature(captured[0], public_key_pem)


# ---------------------------------------------------------------------------
# Shared-inbox deduplication
# ---------------------------------------------------------------------------


class TestSharedInboxDeduplication:
    """Multiple followers sharing one inbox receive exactly one delivery."""

    async def test_two_followers_one_delivery(
        self,
        app: Quart,
        public_key_pem: str,
    ) -> None:
        """Two followers with the same shared inbox → one POST."""
        captured: list[dict[str, Any]] = []
        client_cls = _make_capturing_client(captured, status_code=202)

        domain, username, private_key_pem, key_id = await _delivery_ctx(app)
        session_factory = app.config["DB_SESSION_FACTORY"]
        semaphore = app.config["DELIVERY_SEMAPHORE"]

        async with session_factory() as session:
            from app.repositories.follower import FollowerRepository

            repo = FollowerRepository(session)
            for i in range(2):
                f = Follower(
                    id=uuid.uuid4(),
                    actor_uri=f"https://shared.example.com/users/user{i}",
                    inbox_url=f"https://shared.example.com/users/user{i}/inbox",
                    shared_inbox_url=MOCK_INBOX_URL,
                    status="accepted",
                )
                await repo.add(f)
            await repo.commit()

        async with session_factory() as session:
            note_svc = NoteService(session, domain, username)
            note = await note_svc.create("Shared inbox test.")
            actor_uri = f"https://{domain}/{username}"
            activity = build_create_activity(note, actor_uri)
            delivery_svc = DeliveryService(session)
            items = await delivery_svc.fan_out(activity)

        assert len(items) == 1

        with patch("app.federation.delivery.httpx.AsyncClient", client_cls):
            dispatch_new_items(
                items,
                session_factory=session_factory,
                private_key_pem=private_key_pem,
                key_id=key_id,
                semaphore=semaphore,
            )
            await asyncio.sleep(0.2)

        assert len(captured) == 1


# ---------------------------------------------------------------------------
# Retry on failure
# ---------------------------------------------------------------------------


class TestRetryOnFailure:
    """A delivery that fails is retried after backoff expires."""

    async def test_retry_after_initial_http_error(
        self,
        app: Quart,
        follower: Follower,
        public_key_pem: str,
    ) -> None:
        """First attempt returns 500; second attempt succeeds after backoff."""
        from app.repositories.delivery_queue import DeliveryQueueRepository

        domain, username, private_key_pem, key_id = await _delivery_ctx(app)
        session_factory = app.config["DB_SESSION_FACTORY"]
        semaphore = app.config["DELIVERY_SEMAPHORE"]

        async with session_factory() as session:
            note_svc = NoteService(session, domain, username)
            note = await note_svc.create("Retry test note.")
            actor_uri = f"https://{domain}/{username}"
            activity = build_create_activity(note, actor_uri)
            delivery_svc = DeliveryService(session)
            items = await delivery_svc.fan_out(activity)

        # First attempt: server returns 500.
        fail_captured: list[dict[str, Any]] = []
        fail_client = _make_capturing_client(fail_captured, status_code=500)

        with patch("app.federation.delivery.httpx.AsyncClient", fail_client):
            dispatch_new_items(
                items,
                session_factory=session_factory,
                private_key_pem=private_key_pem,
                key_id=key_id,
                semaphore=semaphore,
            )
            await asyncio.sleep(0.2)

        assert len(fail_captured) == 1

        # Queue entry should have attempts=1 and a future retry time.
        async with session_factory() as session:
            repo = DeliveryQueueRepository(session)
            pending = await repo.get_pending()
        assert len(pending) == 1
        assert pending[0].attempts == 1
        assert pending[0].next_retry_at is not None

        # Backdate retry time so the retry loop picks it up immediately.
        async with session_factory() as session:
            repo = DeliveryQueueRepository(session)
            pending = await repo.get_pending()
            for item in pending:
                item.next_retry_at = datetime(2000, 1, 1, tzinfo=UTC)
            await session.commit()

        # Second attempt: server returns 202.
        success_captured: list[dict[str, Any]] = []
        success_client = _make_capturing_client(success_captured, status_code=202)

        with patch("app.federation.delivery.httpx.AsyncClient", success_client):
            await _retry_loop_step(
                session_factory=session_factory,
                private_key_pem=private_key_pem,
                key_id=key_id,
                semaphore=semaphore,
            )
            await asyncio.sleep(0.2)

        assert len(success_captured) == 1
        body = json.loads(success_captured[0]["content"])
        assert body["type"] == "Create"
        assert _verify_captured_signature(success_captured[0], public_key_pem)

        # Queue entry should now be delivered.
        async with session_factory() as session:
            repo = DeliveryQueueRepository(session)
            all_items = await repo.get_all()
        assert all(item.status == "delivered" for item in all_items)


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------


class TestCrashRecovery:
    """startup_recovery re-dispatches pending items from before a crash."""

    async def test_pending_item_re_delivered_on_startup(
        self,
        app: Quart,
        follower: Follower,
        public_key_pem: str,
    ) -> None:
        """A queue entry never dispatched is delivered by startup_recovery."""
        from app.models.delivery_queue import DeliveryQueue
        from app.repositories.delivery_queue import DeliveryQueueRepository

        domain, username, private_key_pem, key_id = await _delivery_ctx(app)
        session_factory = app.config["DB_SESSION_FACTORY"]
        semaphore = app.config["DELIVERY_SEMAPHORE"]

        # Write a queue entry without dispatching (simulates a crash mid-flight).
        activity = {
            "@context": "https://www.w3.org/ns/activitystreams",
            "type": "Create",
            "actor": f"https://{domain}/{username}",
            "id": f"https://{domain}/notes/{uuid.uuid4()}/activity",
            "object": {
                "type": "Note",
                "content": "<p>Crash recovery test.</p>",
            },
        }
        async with session_factory() as session:
            repo = DeliveryQueueRepository(session)
            orphan = DeliveryQueue(
                id=uuid.uuid4(),
                activity_json=json.dumps(activity),
                target_inbox=MOCK_INBOX_URL,
                status="pending",
                attempts=0,
            )
            await repo.add(orphan)
            await repo.commit()

        captured: list[dict[str, Any]] = []
        client_cls = _make_capturing_client(captured, status_code=202)

        with patch("app.federation.delivery.httpx.AsyncClient", client_cls):
            count = await startup_recovery(
                session_factory=session_factory,
                private_key_pem=private_key_pem,
                key_id=key_id,
                semaphore=semaphore,
            )
            await asyncio.sleep(0.2)

        assert count == 1
        assert len(captured) == 1
        body = json.loads(captured[0]["content"])
        assert body["type"] == "Create"
        assert _verify_captured_signature(captured[0], public_key_pem)
