"""Integration tests for outgoing Follow/Unfollow mechanics and collection endpoints.

Tests :func:`app.federation.follow.send_follow` and
:func:`app.federation.follow.send_unfollow` end-to-end using a pre-seeded
remote actor, mocked HTTP delivery, and a real temporary SQLite database.

Also tests the ``GET /{username}/followers`` and ``GET /{username}/following``
collection endpoints added to :mod:`app.public.routes`.

Test scenarios
--------------
- ``send_follow`` creates a ``Following`` record with status ``"pending"``
  and enqueues a ``Follow`` activity for the remote inbox.
- ``send_follow`` is idempotent: calling again on a ``"pending"`` follow
  returns the existing record without creating a duplicate.
- ``send_follow`` on a ``"rejected"`` follow re-activates it to ``"pending"``.
- ``send_unfollow`` delivers ``Undo{Follow}`` and removes the record.
- ``send_unfollow`` with no record is a silent no-op.
- Followers collection: root and paginated page return correct AP JSON.
- Following collection: root and paginated page return correct AP JSON.
- Wrong username returns 404 for both collection endpoints.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from quart import Quart

from app import create_app
from app.models.follower import Follower
from app.models.following import Following
from app.models.remote_actor import RemoteActor

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REMOTE_ACTOR_URI = "https://remote.example.com/users/bob"
REMOTE_INBOX_URL = f"{REMOTE_ACTOR_URI}/inbox"
LOCAL_DOMAIN = "test.example.com"
LOCAL_USERNAME = "testuser"
LOCAL_ACTOR_URI = f"https://{LOCAL_DOMAIN}/{LOCAL_USERNAME}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_capturing_delivery_client(
    captured: list[dict[str, Any]],
) -> Any:
    """Return a mock httpx client that records POST calls.

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
    """Pump the event loop until all pending background tasks complete.

    Args:
        timeout: Maximum time to wait in seconds.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
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
async def client(app: Quart) -> Any:
    """Create a test client."""
    return app.test_client()


async def _seed_remote_actor(app: Quart) -> RemoteActor:
    """Seed a RemoteActor record in the test database.

    Args:
        app: The test Quart application.

    Returns:
        The created ``RemoteActor`` instance.
    """
    session_factory = app.config["DB_SESSION_FACTORY"]
    async with session_factory() as session:
        from app.repositories.remote_actor import RemoteActorRepository

        repo = RemoteActorRepository(session)
        actor = RemoteActor(
            uri=REMOTE_ACTOR_URI,
            display_name="Bob",
            handle="bob@remote.example.com",
            inbox_url=REMOTE_INBOX_URL,
            shared_inbox_url=None,
            public_key="dummy-public-key-pem",
            fetched_at=datetime.now(UTC),
        )
        await repo.add(actor)
        await session.commit()
        return actor


async def _seed_followers(app: Quart, count: int) -> list[Follower]:
    """Seed accepted Follower records in the test database.

    Args:
        app: The test Quart application.
        count: Number of followers to create.

    Returns:
        The list of created ``Follower`` records.
    """
    session_factory = app.config["DB_SESSION_FACTORY"]
    async with session_factory() as session:
        from app.repositories.follower import FollowerRepository

        repo = FollowerRepository(session)
        followers = []
        for i in range(count):
            f = Follower(
                actor_uri=f"https://remote.example.com/users/follower{i}",
                inbox_url=f"https://remote.example.com/users/follower{i}/inbox",
                status="accepted",
            )
            await repo.add(f)
            followers.append(f)
        await session.commit()
        return followers


async def _seed_following(app: Quart, count: int) -> list[Following]:
    """Seed accepted Following records in the test database.

    Args:
        app: The test Quart application.
        count: Number of following records to create.

    Returns:
        The list of created ``Following`` records.
    """
    session_factory = app.config["DB_SESSION_FACTORY"]
    async with session_factory() as session:
        from app.repositories.following import FollowingRepository

        repo = FollowingRepository(session)
        followings = []
        for i in range(count):
            f = Following(
                actor_uri=f"https://remote.example.com/users/followed{i}",
                inbox_url=f"https://remote.example.com/users/followed{i}/inbox",
                status="accepted",
            )
            await repo.add(f)
            followings.append(f)
        await session.commit()
        return followings


# ---------------------------------------------------------------------------
# send_follow tests
# ---------------------------------------------------------------------------


class TestSendFollow:
    """Tests for :func:`app.federation.follow.send_follow`."""

    async def test_creates_pending_following_record(self, app: Quart) -> None:
        """send_follow creates a Following record with status ``"pending"``."""
        await _seed_remote_actor(app)
        captured: list[dict[str, Any]] = []

        with patch(
            "app.federation.delivery.get_http_client",
            return_value=_make_capturing_delivery_client(captured),
        ):
            async with app.app_context():
                from app.federation.follow import send_follow

                session_factory = app.config["DB_SESSION_FACTORY"]
                async with session_factory() as session:
                    following = await send_follow(
                        REMOTE_ACTOR_URI,
                        session=session,
                        session_factory=session_factory,
                        private_key_pem=app.config.get("INBOX_PRIVATE_KEY_PEM", "dummy"),
                        key_id=app.config.get("INBOX_KEY_ID", "dummy-key-id"),
                        semaphore=app.config["DELIVERY_SEMAPHORE"],
                        domain=LOCAL_DOMAIN,
                        username=LOCAL_USERNAME,
                    )
                    assert following.status == "pending"
                    assert following.actor_uri == REMOTE_ACTOR_URI

        # Verify the DB record persisted.
        async with app.app_context():
            session_factory = app.config["DB_SESSION_FACTORY"]
            async with session_factory() as session:
                from app.repositories.following import FollowingRepository

                repo = FollowingRepository(session)
                record = await repo.get_by_actor_uri(REMOTE_ACTOR_URI)
                assert record is not None
                assert record.status == "pending"

    async def test_delivers_follow_activity_to_inbox(self, app: Quart) -> None:
        """send_follow enqueues a Follow activity delivered to the remote inbox."""
        await _seed_remote_actor(app)
        captured: list[dict[str, Any]] = []

        with patch(
            "app.federation.delivery.get_http_client",
            return_value=_make_capturing_delivery_client(captured),
        ):
            async with app.app_context():
                from app.federation.follow import send_follow

                session_factory = app.config["DB_SESSION_FACTORY"]
                async with session_factory() as session:
                    await send_follow(
                        REMOTE_ACTOR_URI,
                        session=session,
                        session_factory=session_factory,
                        private_key_pem=app.config.get("INBOX_PRIVATE_KEY_PEM", "dummy"),
                        key_id=app.config.get("INBOX_KEY_ID", "dummy-key-id"),
                        semaphore=app.config["DELIVERY_SEMAPHORE"],
                        domain=LOCAL_DOMAIN,
                        username=LOCAL_USERNAME,
                    )
            await _wait_for_tasks()

        assert len(captured) == 1
        assert captured[0]["url"] == REMOTE_INBOX_URL
        body = json.loads(captured[0]["content"])
        assert body["type"] == "Follow"
        assert body["actor"] == LOCAL_ACTOR_URI
        assert body["object"] == REMOTE_ACTOR_URI

    async def test_idempotent_on_pending(self, app: Quart) -> None:
        """Calling send_follow twice when already pending returns the same record."""
        await _seed_remote_actor(app)
        captured: list[dict[str, Any]] = []

        with patch(
            "app.federation.delivery.get_http_client",
            return_value=_make_capturing_delivery_client(captured),
        ):
            async with app.app_context():
                from app.federation.follow import send_follow

                session_factory = app.config["DB_SESSION_FACTORY"]
                private_key_pem: str = app.config["INBOX_PRIVATE_KEY_PEM"]
                key_id: str = app.config["INBOX_KEY_ID"]
                async with session_factory() as session:
                    first = await send_follow(
                        REMOTE_ACTOR_URI,
                        session=session,
                        session_factory=session_factory,
                        private_key_pem=private_key_pem,
                        key_id=key_id,
                        semaphore=app.config["DELIVERY_SEMAPHORE"],
                        domain=LOCAL_DOMAIN,
                        username=LOCAL_USERNAME,
                    )
                first_id = first.id
                # Second call — new session.
                async with session_factory() as session:
                    second = await send_follow(
                        REMOTE_ACTOR_URI,
                        session=session,
                        session_factory=session_factory,
                        private_key_pem=private_key_pem,
                        key_id=key_id,
                        semaphore=app.config["DELIVERY_SEMAPHORE"],
                        domain=LOCAL_DOMAIN,
                        username=LOCAL_USERNAME,
                    )
                    assert first_id == second.id

        # Only one delivery should have been enqueued (from the first call).
        await _wait_for_tasks()
        assert len(captured) == 1

    async def test_reactivates_rejected_follow(self, app: Quart) -> None:
        """send_follow resets a rejected follow back to pending."""
        await _seed_remote_actor(app)

        # Pre-seed a rejected Following record.
        session_factory = app.config["DB_SESSION_FACTORY"]
        async with app.app_context(), session_factory() as session:
            from app.repositories.following import FollowingRepository

            repo = FollowingRepository(session)
            rejected = Following(
                actor_uri=REMOTE_ACTOR_URI,
                inbox_url=REMOTE_INBOX_URL,
                status="rejected",
            )
            await repo.add(rejected)
            await session.commit()

        captured: list[dict[str, Any]] = []
        with patch(
            "app.federation.delivery.get_http_client",
            return_value=_make_capturing_delivery_client(captured),
        ):
            async with app.app_context():
                from app.federation.follow import send_follow

                private_key_pem: str = app.config["INBOX_PRIVATE_KEY_PEM"]
                key_id: str = app.config["INBOX_KEY_ID"]
                async with session_factory() as session:
                    result = await send_follow(
                        REMOTE_ACTOR_URI,
                        session=session,
                        session_factory=session_factory,
                        private_key_pem=private_key_pem,
                        key_id=key_id,
                        semaphore=app.config["DELIVERY_SEMAPHORE"],
                        domain=LOCAL_DOMAIN,
                        username=LOCAL_USERNAME,
                    )
                    assert result.status == "pending"

        await _wait_for_tasks()
        assert len(captured) == 1

    async def test_raises_if_actor_not_fetchable(self, app: Quart) -> None:
        """send_follow raises ValueError if the remote actor cannot be fetched."""
        # Do NOT seed the remote actor; the service should fail to resolve it.
        with patch(
            "app.services.remote_actor.RemoteActorService.get_by_uri",
            new_callable=AsyncMock,
            return_value=None,
        ):
            async with app.app_context():
                from app.federation.follow import send_follow

                session_factory = app.config["DB_SESSION_FACTORY"]
                async with session_factory() as session:
                    with pytest.raises(ValueError, match="Could not fetch remote actor"):
                        await send_follow(
                            "https://unreachable.example.com/users/ghost",
                            session=session,
                            session_factory=session_factory,
                            private_key_pem="dummy",
                            key_id="dummy-key-id",
                            semaphore=app.config["DELIVERY_SEMAPHORE"],
                            domain=LOCAL_DOMAIN,
                            username=LOCAL_USERNAME,
                        )


# ---------------------------------------------------------------------------
# send_unfollow tests
# ---------------------------------------------------------------------------


class TestSendUnfollow:
    """Tests for :func:`app.federation.follow.send_unfollow`."""

    async def test_delivers_undo_follow_activity(self, app: Quart) -> None:
        """send_unfollow delivers an Undo{Follow} activity to the remote inbox."""
        await _seed_remote_actor(app)
        captured: list[dict[str, Any]] = []

        async with app.app_context():
            session_factory = app.config["DB_SESSION_FACTORY"]
            private_key_pem: str = app.config["INBOX_PRIVATE_KEY_PEM"]
            key_id: str = app.config["INBOX_KEY_ID"]

        # First follow.
        with patch(
            "app.federation.delivery.get_http_client",
            return_value=_make_capturing_delivery_client(captured),
        ):
            async with app.app_context():
                from app.federation.follow import send_follow

                session_factory = app.config["DB_SESSION_FACTORY"]
                async with session_factory() as session:
                    await send_follow(
                        REMOTE_ACTOR_URI,
                        session=session,
                        session_factory=session_factory,
                        private_key_pem=private_key_pem,
                        key_id=key_id,
                        semaphore=app.config["DELIVERY_SEMAPHORE"],
                        domain=LOCAL_DOMAIN,
                        username=LOCAL_USERNAME,
                    )
            await _wait_for_tasks()

        captured.clear()

        # Now unfollow.
        with patch(
            "app.federation.delivery.get_http_client",
            return_value=_make_capturing_delivery_client(captured),
        ):
            async with app.app_context():
                from app.federation.follow import send_unfollow

                session_factory = app.config["DB_SESSION_FACTORY"]
                async with session_factory() as session:
                    await send_unfollow(
                        REMOTE_ACTOR_URI,
                        session=session,
                        session_factory=session_factory,
                        private_key_pem=private_key_pem,
                        key_id=key_id,
                        semaphore=app.config["DELIVERY_SEMAPHORE"],
                        domain=LOCAL_DOMAIN,
                        username=LOCAL_USERNAME,
                    )
            await _wait_for_tasks()

        assert len(captured) == 1
        assert captured[0]["url"] == REMOTE_INBOX_URL
        body = json.loads(captured[0]["content"])
        assert body["type"] == "Undo"
        assert body["object"]["type"] == "Follow"
        assert body["object"]["actor"] == LOCAL_ACTOR_URI
        assert body["object"]["object"] == REMOTE_ACTOR_URI

    async def test_removes_following_record(self, app: Quart) -> None:
        """send_unfollow deletes the Following record from the database."""
        await _seed_remote_actor(app)

        with patch(
            "app.federation.delivery.get_http_client",
            return_value=_make_capturing_delivery_client([]),
        ):
            async with app.app_context():
                from app.federation.follow import send_follow, send_unfollow

                session_factory = app.config["DB_SESSION_FACTORY"]
                async with session_factory() as session:
                    await send_follow(
                        REMOTE_ACTOR_URI,
                        session=session,
                        session_factory=session_factory,
                        private_key_pem="dummy",
                        key_id="dummy-key-id",
                        semaphore=app.config["DELIVERY_SEMAPHORE"],
                        domain=LOCAL_DOMAIN,
                        username=LOCAL_USERNAME,
                    )
                async with session_factory() as session:
                    await send_unfollow(
                        REMOTE_ACTOR_URI,
                        session=session,
                        session_factory=session_factory,
                        private_key_pem="dummy",
                        key_id="dummy-key-id",
                        semaphore=app.config["DELIVERY_SEMAPHORE"],
                        domain=LOCAL_DOMAIN,
                        username=LOCAL_USERNAME,
                    )

        async with app.app_context():
            session_factory = app.config["DB_SESSION_FACTORY"]
            async with session_factory() as session:
                from app.repositories.following import FollowingRepository

                repo = FollowingRepository(session)
                record = await repo.get_by_actor_uri(REMOTE_ACTOR_URI)
                assert record is None

    async def test_noop_when_not_following(self, app: Quart) -> None:
        """send_unfollow is a silent no-op when no Following record exists."""
        captured: list[dict[str, Any]] = []
        with patch(
            "app.federation.delivery.get_http_client",
            return_value=_make_capturing_delivery_client(captured),
        ):
            async with app.app_context():
                from app.federation.follow import send_unfollow

                session_factory = app.config["DB_SESSION_FACTORY"]
                async with session_factory() as session:
                    # Should not raise, should not deliver anything.
                    await send_unfollow(
                        "https://remote.example.com/users/nobody",
                        session=session,
                        session_factory=session_factory,
                        private_key_pem="dummy",
                        key_id="dummy-key-id",
                        semaphore=app.config["DELIVERY_SEMAPHORE"],
                        domain=LOCAL_DOMAIN,
                        username=LOCAL_USERNAME,
                    )

        await _wait_for_tasks()
        assert len(captured) == 0


# ---------------------------------------------------------------------------
# Collection endpoint tests
# ---------------------------------------------------------------------------


class TestFollowersCollection:
    """Tests for ``GET /{username}/followers``."""

    async def test_root_returns_ordered_collection(self, app: Quart) -> None:
        """Root endpoint returns an OrderedCollection with correct fields."""
        async with app.test_client() as client:
            resp = await client.get(
                f"/{LOCAL_USERNAME}/followers",
                headers={"Accept": "application/activity+json"},
            )
        assert resp.status_code == 200
        assert "application/activity+json" in resp.content_type
        data = json.loads(await resp.get_data())
        assert data["type"] == "OrderedCollection"
        assert "totalItems" in data
        assert "first" in data
        assert "last" in data
        assert f"/{LOCAL_USERNAME}/followers" in data["id"]

    async def test_root_total_items_reflects_accepted_count(self, app: Quart) -> None:
        """Root totalItems matches the number of accepted followers."""
        await _seed_followers(app, 3)
        async with app.test_client() as client:
            resp = await client.get(
                f"/{LOCAL_USERNAME}/followers",
                headers={"Accept": "application/activity+json"},
            )
        data = json.loads(await resp.get_data())
        assert data["totalItems"] == 3

    async def test_page_returns_ordered_collection_page(self, app: Quart) -> None:
        """``?page=1`` returns an OrderedCollectionPage."""
        await _seed_followers(app, 2)
        async with app.test_client() as client:
            resp = await client.get(
                f"/{LOCAL_USERNAME}/followers?page=1",
                headers={"Accept": "application/activity+json"},
            )
        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert data["type"] == "OrderedCollectionPage"
        assert data["partOf"].endswith(f"/{LOCAL_USERNAME}/followers")
        assert len(data["orderedItems"]) == 2
        # Items are actor URIs (strings).
        for item in data["orderedItems"]:
            assert isinstance(item, str)
            assert item.startswith("https://")

    async def test_page_no_next_when_fewer_than_page_size(self, app: Quart) -> None:
        """No ``next`` link when the page has fewer items than the page size."""
        await _seed_followers(app, 1)
        async with app.test_client() as client:
            resp = await client.get(
                f"/{LOCAL_USERNAME}/followers?page=1",
                headers={"Accept": "application/activity+json"},
            )
        data = json.loads(await resp.get_data())
        assert "next" not in data

    async def test_page_2_includes_prev_link(self, app: Quart) -> None:
        """Page 2 includes a ``prev`` link back to page 1."""
        await _seed_followers(app, 1)
        async with app.test_client() as client:
            resp = await client.get(
                f"/{LOCAL_USERNAME}/followers?page=2",
                headers={"Accept": "application/activity+json"},
            )
        data = json.loads(await resp.get_data())
        assert "prev" in data
        assert "page=1" in data["prev"]

    async def test_wrong_username_returns_404(self, app: Quart) -> None:
        """Wrong username path returns 404."""
        async with app.test_client() as client:
            resp = await client.get(
                "/nonexistent/followers",
                headers={"Accept": "application/activity+json"},
            )
        assert resp.status_code == 404

    async def test_context_present(self, app: Quart) -> None:
        """Root response includes the ActivityStreams @context."""
        async with app.test_client() as client:
            resp = await client.get(
                f"/{LOCAL_USERNAME}/followers",
                headers={"Accept": "application/activity+json"},
            )
        data = json.loads(await resp.get_data())
        assert "@context" in data


class TestFollowingCollection:
    """Tests for ``GET /{username}/following``."""

    async def test_root_returns_ordered_collection(self, app: Quart) -> None:
        """Root endpoint returns an OrderedCollection with correct fields."""
        async with app.test_client() as client:
            resp = await client.get(
                f"/{LOCAL_USERNAME}/following",
                headers={"Accept": "application/activity+json"},
            )
        assert resp.status_code == 200
        assert "application/activity+json" in resp.content_type
        data = json.loads(await resp.get_data())
        assert data["type"] == "OrderedCollection"
        assert "totalItems" in data
        assert "first" in data
        assert "last" in data

    async def test_root_total_items_reflects_accepted_count(self, app: Quart) -> None:
        """Root totalItems matches the number of accepted following relationships."""
        await _seed_following(app, 5)
        async with app.test_client() as client:
            resp = await client.get(
                f"/{LOCAL_USERNAME}/following",
                headers={"Accept": "application/activity+json"},
            )
        data = json.loads(await resp.get_data())
        assert data["totalItems"] == 5

    async def test_page_returns_actor_uris(self, app: Quart) -> None:
        """``?page=1`` orderedItems are actor URI strings."""
        await _seed_following(app, 2)
        async with app.test_client() as client:
            resp = await client.get(
                f"/{LOCAL_USERNAME}/following?page=1",
                headers={"Accept": "application/activity+json"},
            )
        data = json.loads(await resp.get_data())
        assert data["type"] == "OrderedCollectionPage"
        assert len(data["orderedItems"]) == 2
        for item in data["orderedItems"]:
            assert isinstance(item, str)

    async def test_wrong_username_returns_404(self, app: Quart) -> None:
        """Wrong username path returns 404."""
        async with app.test_client() as client:
            resp = await client.get(
                "/nonexistent/following",
                headers={"Accept": "application/activity+json"},
            )
        assert resp.status_code == 404

    async def test_context_present(self, app: Quart) -> None:
        """Root response includes the ActivityStreams @context."""
        async with app.test_client() as client:
            resp = await client.get(
                f"/{LOCAL_USERNAME}/following",
                headers={"Accept": "application/activity+json"},
            )
        data = json.loads(await resp.get_data())
        assert "@context" in data
