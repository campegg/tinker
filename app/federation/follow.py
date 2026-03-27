"""Outgoing Follow and Unfollow mechanics for ActivityPub federation.

Provides pure activity builders and service functions for sending Follow and
Undo{Follow} activities to remote actors.

Activity flow
-------------
1. Caller invokes :func:`send_follow` with the target actor URI.
2. A ``Following`` record is created with status ``"pending"``.
3. A signed ``Follow`` activity is enqueued and dispatched.
4. When the remote server accepts, it sends ``Accept{Follow}`` to our inbox
   which updates the status to ``"accepted"`` (handled in
   :mod:`app.federation.inbox`).
5. :func:`send_unfollow` sends ``Undo{Follow}`` and removes the record.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.core.config import make_actor_uri
from app.federation.delivery import DeliveryService, dispatch_new_items
from app.federation.outbox import AP_CONTEXT
from app.models.following import Following
from app.repositories.following import FollowingRepository
from app.services.remote_actor import RemoteActorService

if TYPE_CHECKING:
    import asyncio
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Activity builders — pure functions, no I/O
# ---------------------------------------------------------------------------


def build_follow_activity(
    actor_uri: str,
    target_uri: str,
    activity_id: str,
) -> dict[str, Any]:
    """Build a ``Follow`` activity directed at a remote actor.

    Args:
        actor_uri: The local actor's canonical AP URI
            (e.g. ``"https://example.com/user"``).
        target_uri: The URI of the remote actor to follow.
        activity_id: The unique URI for this Follow activity
            (e.g. ``"https://example.com/follows/{uuid}"``).

    Returns:
        A JSON-LD dictionary representing the ``Follow`` activity,
        ready for delivery.
    """
    return {
        "@context": AP_CONTEXT,
        "id": activity_id,
        "type": "Follow",
        "actor": actor_uri,
        "object": target_uri,
    }


def build_undo_follow_activity(
    actor_uri: str,
    target_uri: str,
    follow_activity_id: str,
) -> dict[str, Any]:
    """Build an ``Undo{Follow}`` activity to unfollow a remote actor.

    The embedded object is the original ``Follow`` activity, identified by
    its URI.  Remote servers use this to remove the follow relationship.

    Args:
        actor_uri: The local actor's canonical AP URI.
        target_uri: The URI of the remote actor being unfollowed.
        follow_activity_id: The URI of the original ``Follow`` activity
            (derived from the ``Following`` record's UUID).

    Returns:
        A JSON-LD dictionary representing the ``Undo{Follow}`` activity,
        ready for delivery.
    """
    return {
        "@context": AP_CONTEXT,
        "id": f"{follow_activity_id}#undo",
        "type": "Undo",
        "actor": actor_uri,
        "object": {
            "id": follow_activity_id,
            "type": "Follow",
            "actor": actor_uri,
            "object": target_uri,
        },
    }


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------


def _follow_activity_id(domain: str, following_id: uuid.UUID) -> str:
    """Derive the canonical AP URI for a Follow activity.

    Args:
        domain: The local instance domain.
        following_id: The UUID primary key of the ``Following`` record.

    Returns:
        A fully-qualified URI string for the Follow activity.
    """
    return f"https://{domain}/follows/{following_id}"


async def send_follow(
    target_actor_uri: str,
    *,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    private_key_pem: str,
    key_id: str,
    semaphore: asyncio.Semaphore,
    domain: str,
    username: str,
) -> Following:
    """Send a ``Follow`` activity to a remote actor's inbox.

    Idempotent: if a ``Following`` record already exists with status
    ``"pending"`` or ``"accepted"``, the existing record is returned
    without creating a new one or re-sending the activity.

    Steps:

    1. Check for an existing ``Following`` record.
    2. Fetch the remote actor document to obtain the inbox URL and
       cached metadata (display name, avatar URL).
    3. Create the ``Following`` record with status ``"pending"``.
    4. Build the ``Follow`` activity and enqueue delivery.
    5. Dispatch the background delivery task.

    Args:
        target_actor_uri: The canonical AP URI of the remote actor to follow.
        session: The current async database session.
        session_factory: Factory for per-task database sessions used by the
            delivery background task.
        private_key_pem: PEM-encoded RSA private key for signing.
        key_id: Key ID URI for the HTTP Signature header.
        semaphore: Shared concurrency limiter for outbound requests.
        domain: The local instance domain (e.g. ``"example.com"``).
        username: The local actor username.

    Returns:
        The ``Following`` record (new or pre-existing).

    Raises:
        ValueError: If the remote actor cannot be fetched and no inbox URL
            is available.
    """
    actor_uri = make_actor_uri(domain, username)
    following_repo = FollowingRepository(session)

    # Idempotency: return existing record if follow is already in progress.
    existing = await following_repo.get_by_actor_uri(target_actor_uri)
    if existing is not None and existing.status in ("pending", "accepted"):
        logger.debug(
            "send_follow: already %r for %r — skipping",
            existing.status,
            target_actor_uri,
        )
        return existing

    # Fetch the remote actor to get inbox URL and cached display info.
    actor_svc = RemoteActorService(session)
    remote = await actor_svc.get_by_uri(target_actor_uri)
    if remote is None:
        raise ValueError(f"Could not fetch remote actor document for {target_actor_uri!r}")

    # Create (or reuse if previously rejected) the Following record.
    if existing is not None:
        # Reactivate a previously rejected follow.
        existing.status = "pending"
        existing.inbox_url = remote.inbox_url
        existing.display_name = remote.display_name
        existing.avatar_url = remote.avatar_url
        await session.flush()
        following = existing
    else:
        following = Following(
            actor_uri=target_actor_uri,
            inbox_url=remote.inbox_url,
            display_name=remote.display_name,
            avatar_url=remote.avatar_url,
            status="pending",
        )
        await following_repo.add(following)

    await session.commit()

    # Build and enqueue the Follow activity.
    follow_id = _follow_activity_id(domain, following.id)
    activity = build_follow_activity(actor_uri, target_actor_uri, follow_id)

    delivery_svc = DeliveryService(session)
    items = [await delivery_svc.deliver_to_inbox(activity, remote.inbox_url)]
    dispatch_new_items(
        items,
        session_factory=session_factory,
        private_key_pem=private_key_pem,
        key_id=key_id,
        semaphore=semaphore,
    )

    logger.info("Sent Follow to %r (activity_id=%r)", target_actor_uri, follow_id)
    return following


async def send_unfollow(
    target_actor_uri: str,
    *,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    private_key_pem: str,
    key_id: str,
    semaphore: asyncio.Semaphore,
    domain: str,
    username: str,
) -> None:
    """Send an ``Undo{Follow}`` activity to unfollow a remote actor.

    No-op if no ``Following`` record exists for the given URI.

    Steps:

    1. Look up the ``Following`` record.
    2. Build the ``Undo{Follow}`` activity referencing the original Follow.
    3. Deliver to the remote actor's inbox.
    4. Delete the ``Following`` record.

    Args:
        target_actor_uri: The canonical AP URI of the remote actor to unfollow.
        session: The current async database session.
        session_factory: Factory for per-task database sessions.
        private_key_pem: PEM-encoded RSA private key for signing.
        key_id: Key ID URI for the HTTP Signature header.
        semaphore: Shared concurrency limiter for outbound requests.
        domain: The local instance domain.
        username: The local actor username.
    """
    actor_uri = make_actor_uri(domain, username)
    following_repo = FollowingRepository(session)

    following = await following_repo.get_by_actor_uri(target_actor_uri)
    if following is None:
        logger.debug("send_unfollow: no Following record for %r — no-op", target_actor_uri)
        return

    inbox_url = following.inbox_url
    if inbox_url is None:
        logger.warning(
            "send_unfollow: Following record for %r has no inbox_url — deleting without delivery",
            target_actor_uri,
        )
        await following_repo.delete(following)
        await session.commit()
        return

    follow_id = _follow_activity_id(domain, following.id)
    activity = build_undo_follow_activity(actor_uri, target_actor_uri, follow_id)

    delivery_svc = DeliveryService(session)
    items = [await delivery_svc.deliver_to_inbox(activity, inbox_url)]

    await following_repo.delete(following)
    await session.commit()

    dispatch_new_items(
        items,
        session_factory=session_factory,
        private_key_pem=private_key_pem,
        key_id=key_id,
        semaphore=semaphore,
    )

    logger.info("Sent Undo{Follow} to %r (follow_id=%r)", target_actor_uri, follow_id)
