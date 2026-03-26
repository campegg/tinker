"""Incoming ActivityPub activity processing.

Handles rate limiting, dispatching of async processing tasks, and all
supported incoming activity types:

- ``Follow``            → auto-Accept, store follower, send ``Accept{Follow}``
- ``Undo{Follow}``      → remove follower record
- ``Create{Note}``      → store in timeline if from a followed actor
- ``Announce``          → store in timeline if from a followed actor
- ``Like``              → store ``Like`` record; notify if on a local note
- ``Delete``            → remove referenced object from timeline / likes
- ``Update``            → overwrite locally cached timeline content
- ``Undo{Like}``        → remove corresponding ``Like`` record
- ``Undo{Announce}``    → remove corresponding timeline entry
- ``Accept{Follow}``    → mark outgoing follow as accepted
- ``Reject{Follow}``    → mark outgoing follow as rejected, remove record

All incoming HTML content is sanitised with ``nh3`` before storage.

Processing is always asynchronous: the HTTP endpoint returns ``202 Accepted``
immediately and dispatches a background task via :func:`process_activity`.
Each background task creates its own database session so it never shares
session state with the request context.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx
import nh3

from app.models.follower import Follower
from app.models.like import Like
from app.models.notification import Notification
from app.models.timeline_item import TimelineItem
from app.repositories.follower import FollowerRepository
from app.repositories.following import FollowingRepository
from app.repositories.like import LikeRepository
from app.repositories.notification import NotificationRepository
from app.repositories.timeline_item import TimelineItemRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# nh3 sanitisation — strict allowlist for inbound HTML
# ---------------------------------------------------------------------------

_ALLOWED_TAGS: set[str] = {
    "p",
    "br",
    "span",
    "a",
    "strong",
    "b",
    "em",
    "i",
    "code",
    "pre",
    "ul",
    "ol",
    "li",
    "blockquote",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
}

_ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    # Note: "rel" is intentionally absent from "a" — the link_rel parameter
    # in nh3.clean() sets rel automatically and conflicts with a manual entry.
    "a": {"href", "class"},
    "span": {"class"},
    "p": {"class"},
    "pre": {"class"},
    "code": {"class"},
}

# ---------------------------------------------------------------------------
# Rate limiting — per-IP sliding window
# ---------------------------------------------------------------------------

_INBOX_RATE_LIMIT_WINDOW: float = 60.0  # seconds
_INBOX_RATE_LIMIT_MAX: int = 100  # requests per window

_rate_limit_lock: asyncio.Lock = asyncio.Lock()
_inbox_attempts: dict[str, list[float]] = defaultdict(list)

# ---------------------------------------------------------------------------
# Fetch timeout for remote AP objects (used in Announce resolution)
# ---------------------------------------------------------------------------

_FETCH_TIMEOUT_SECONDS: float = 10.0
_USER_AGENT = "Tinker/0.1.0"

# ---------------------------------------------------------------------------
# InboxContext — bundles infrastructure params for background tasks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InboxContext:
    """Infrastructure parameters required by the inbox processing task.

    Passed from the request handler into :func:`process_activity` so the
    background task has everything it needs without touching global state.

    Attributes:
        session_factory: Factory for creating new ``AsyncSession`` instances.
        private_key_pem: PEM-encoded RSA private key for signing outbound
            requests (used when delivering ``Accept{Follow}``).
        key_id: The HTTP Signature key ID URI
            (e.g. ``"https://domain/username#main-key"``).
        semaphore: Semaphore bounding simultaneous outbound HTTP requests.
        domain: The local instance domain (e.g. ``"example.com"``).
        username: The local actor username.
        notification_queue: Queue for emitting real-time notification events
            to the SSE endpoint (WP-16).
        media_path: Absolute path to the media storage directory.  When
            set, remote actor avatars are downloaded and cached locally
            during Follow processing so they are never served from remote
            URLs.  An empty string disables avatar proxying.
    """

    session_factory: async_sessionmaker[AsyncSession]
    private_key_pem: str
    key_id: str
    semaphore: asyncio.Semaphore
    domain: str
    username: str
    notification_queue: asyncio.Queue[dict[str, Any]]
    media_path: str = ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def check_inbox_rate_limit(ip: str) -> bool:
    """Check whether an inbox request from the given IP should be allowed.

    Implements a sliding-window rate limit: at most
    :data:`_INBOX_RATE_LIMIT_MAX` requests within
    :data:`_INBOX_RATE_LIMIT_WINDOW` seconds.  Records the attempt if
    allowed.

    Args:
        ip: The client IP address string.

    Returns:
        ``True`` if the request is within the rate limit and should be
        processed, ``False`` if it exceeds the limit and should be
        rejected with ``429``.
    """
    now = time.monotonic()
    async with _rate_limit_lock:
        cutoff = now - _INBOX_RATE_LIMIT_WINDOW
        _inbox_attempts[ip] = [t for t in _inbox_attempts[ip] if t >= cutoff]
        if len(_inbox_attempts[ip]) >= _INBOX_RATE_LIMIT_MAX:
            return False
        _inbox_attempts[ip].append(now)
        return True


async def process_activity(
    activity: dict[str, Any],
    key_owner_uri: str,
    ctx: InboxContext,
) -> None:
    """Process an incoming ActivityPub activity in a background task.

    Creates its own database session (never shares the request session),
    dispatches to the appropriate handler based on activity type, commits
    on success, and rolls back on error.

    All exceptions are caught and logged — the task must never propagate
    an unhandled exception, since it runs fire-and-forget.

    Args:
        activity: The parsed JSON-LD activity dict.
        key_owner_uri: The actor URI derived from the HTTP Signature
            ``keyId``.  Used to prevent actor spoofing.
        ctx: The :class:`InboxContext` containing infrastructure
            dependencies.
    """
    activity_type = activity.get("type", "")
    activity_id = activity.get("id", "<no id>")

    async with ctx.session_factory() as session:
        try:
            await _dispatch(activity, activity_type, key_owner_uri, session, ctx)
            await session.commit()
        except Exception:
            logger.exception(
                "Error processing activity type=%r id=%r actor=%r",
                activity_type,
                activity_id,
                key_owner_uri,
            )
            await session.rollback()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _sanitize_html(html: str) -> str:
    """Sanitise an HTML string using a strict allowlist.

    Strips all elements and attributes not in the allow-lists.
    External links have ``rel="noopener noreferrer nofollow"`` added
    automatically by ``nh3``.

    Args:
        html: The raw HTML string from a remote ActivityPub object.

    Returns:
        A sanitised HTML string safe for storage and display.
    """
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes={"https", "http"},
        link_rel="noopener noreferrer nofollow",
    )


def _extract_object_id(obj: Any) -> str | None:
    """Extract the ``id`` (URI) from an activity ``object`` field.

    The ``object`` in an AP activity may be either a plain URI string or
    an embedded dict with an ``"id"`` key.

    Args:
        obj: The value of the ``"object"`` field from an AP activity.

    Returns:
        The URI string if one could be extracted, otherwise ``None``.
    """
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        raw = obj.get("id")
        if isinstance(raw, str):
            return raw
    return None


def _is_local_note_uri(uri: str, domain: str) -> bool:
    """Check whether a URI refers to a locally authored note.

    Args:
        uri: The ActivityPub URI to test.
        domain: The local instance domain name.

    Returns:
        ``True`` if ``uri`` starts with ``https://<domain>/notes/``.
    """
    return uri.startswith(f"https://{domain}/notes/")


def _actor_uri_from_key_id(key_id: str) -> str:
    """Derive the actor URI from an HTTP Signature ``keyId``.

    Strips the URL fragment (e.g. ``#main-key``) to obtain the actor's
    canonical URI.  This follows the Mastodon convention where
    ``keyId = "<actor_uri>#main-key"``.

    Args:
        key_id: The ``keyId`` value from the HTTP Signature header.

    Returns:
        The actor URI without the fragment component.
    """
    parsed = urlparse(key_id)
    # Reconstruct without the fragment.
    return parsed._replace(fragment="").geturl()


async def _fetch_ap_object(uri: str) -> dict[str, Any] | None:
    """Fetch a remote ActivityPub object document over HTTP.

    Args:
        uri: The URI of the remote AP object to fetch.

    Returns:
        The parsed JSON dict, or ``None`` if the fetch failed for any
        reason (network error, non-2xx status, or invalid JSON).
    """
    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            response = await client.get(
                uri,
                headers={
                    "Accept": "application/activity+json",
                    "User-Agent": _USER_AGENT,
                },
            )
            response.raise_for_status()
            doc: dict[str, Any] = response.json()
            return doc
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "HTTP %s fetching AP object at %s",
            exc.response.status_code,
            uri,
        )
    except httpx.RequestError as exc:
        logger.warning("Network error fetching AP object at %s: %s", uri, exc)
    except Exception:
        logger.exception("Unexpected error fetching AP object at %s", uri)
    return None


async def _emit_notification(
    notification: Notification,
    ctx: InboxContext,
) -> None:
    """Persist a notification to the database and emit it to the SSE queue.

    The notification must already be added to the session (via the
    ``NotificationRepository``); this function only handles the queue
    emission.

    Args:
        notification: The newly created notification instance.
        ctx: The current :class:`InboxContext`.
    """
    event: dict[str, Any] = {
        "type": notification.type,
        "actor_uri": notification.actor_uri,
        "actor_name": notification.actor_name,
        "object_uri": notification.object_uri,
    }
    try:
        ctx.notification_queue.put_nowait(event)
    except asyncio.QueueFull:
        logger.warning(
            "Notification queue full — dropping real-time event type=%r actor=%r",
            notification.type,
            notification.actor_uri,
        )


# ---------------------------------------------------------------------------
# Activity dispatcher
# ---------------------------------------------------------------------------


async def _dispatch(
    activity: dict[str, Any],
    activity_type: str,
    key_owner_uri: str,
    session: AsyncSession,
    ctx: InboxContext,
) -> None:
    """Dispatch an activity to the appropriate handler.

    Args:
        activity: The parsed activity dict.
        activity_type: The ``type`` field of the activity.
        key_owner_uri: The verified actor URI from the HTTP Signature.
        session: The current database session.
        ctx: The current :class:`InboxContext`.
    """
    if activity_type == "Follow":
        await _handle_follow(activity, key_owner_uri, session, ctx)
    elif activity_type == "Undo":
        await _handle_undo(activity, key_owner_uri, session)
    elif activity_type == "Create":
        await _handle_create(activity, key_owner_uri, session, ctx)
    elif activity_type == "Announce":
        await _handle_announce(activity, key_owner_uri, session, ctx)
    elif activity_type == "Like":
        await _handle_like(activity, key_owner_uri, session, ctx)
    elif activity_type == "Delete":
        await _handle_delete(activity, session)
    elif activity_type == "Update":
        await _handle_update(activity, session)
    elif activity_type == "Accept":
        await _handle_accept(activity, key_owner_uri, session)
    elif activity_type == "Reject":
        await _handle_reject(activity, key_owner_uri, session)
    else:
        logger.debug("Ignoring unsupported activity type %r", activity_type)


# ---------------------------------------------------------------------------
# Activity handlers
# ---------------------------------------------------------------------------


async def _handle_follow(
    activity: dict[str, Any],
    key_owner_uri: str,
    session: AsyncSession,
    ctx: InboxContext,
) -> None:
    """Handle an incoming ``Follow`` activity.

    Stores the follower (or updates an existing record), then sends an
    ``Accept{Follow}`` back to the follower's inbox and creates a
    notification.

    Args:
        activity: The ``Follow`` activity dict.
        key_owner_uri: The verified actor URI (the actor requesting to follow).
        session: The current database session.
        ctx: The current :class:`InboxContext`.
    """
    # The actor field must match the signed key owner — reject mismatches.
    actor_uri = activity.get("actor")
    if not isinstance(actor_uri, str) or actor_uri != key_owner_uri:
        logger.warning(
            "Follow actor %r does not match key owner %r — ignoring",
            actor_uri,
            key_owner_uri,
        )
        return

    # The object being followed must be our local actor.
    local_actor_uri = f"https://{ctx.domain}/{ctx.username}"
    follow_object = activity.get("object")
    if follow_object != local_actor_uri:
        logger.warning(
            "Follow object %r does not match local actor %r — ignoring",
            follow_object,
            local_actor_uri,
        )
        return

    # Fetch the remote actor to get their inbox URL and display info.
    from app.services.remote_actor import RemoteActorService

    actor_svc = RemoteActorService(session)
    remote_actor = await actor_svc.get_by_uri(actor_uri)
    inbox_url = remote_actor.inbox_url if remote_actor else None
    display_name = remote_actor.display_name if remote_actor else None
    remote_avatar_url = remote_actor.avatar_url if remote_actor else None

    if not inbox_url:
        logger.error(
            "Cannot process Follow from %r: no inbox URL available",
            actor_uri,
        )
        return

    # Proxy the remote avatar to local storage so it is never served from
    # a remote URL directly (prevents IP leakage and tracking pixels).
    local_avatar_url: str | None = None
    if remote_avatar_url and ctx.media_path:
        from app.media import proxy_avatar

        local_avatar_url = await proxy_avatar(remote_avatar_url, ctx.media_path)

    # Upsert the follower record.
    follower_repo = FollowerRepository(session)
    existing = await follower_repo.get_by_actor_uri(actor_uri)

    follow_activity_uri: str | None = activity.get("id") or None

    if existing is not None:
        existing.status = "accepted"
        if display_name:
            existing.display_name = display_name
        if local_avatar_url:
            existing.avatar_url = local_avatar_url
        if follow_activity_uri:
            existing.follow_activity_uri = follow_activity_uri
        await session.flush()
        follower = existing
    else:
        shared_inbox_url = remote_actor.shared_inbox_url if remote_actor else None
        follower = Follower(
            actor_uri=actor_uri,
            inbox_url=inbox_url,
            shared_inbox_url=shared_inbox_url,
            display_name=display_name,
            avatar_url=local_avatar_url,
            status="accepted",
            follow_activity_uri=follow_activity_uri,
        )
        follower_repo._session.add(follower)
        await session.flush()

    # Build and deliver the Accept{Follow} activity.
    follow_id = activity.get("id", actor_uri)
    accept_id = f"{local_actor_uri}#accepts/{uuid.uuid4()}"
    accept_activity: dict[str, Any] = {
        "@context": [
            "https://www.w3.org/ns/activitystreams",
            "https://w3id.org/security/v1",
        ],
        "id": accept_id,
        "type": "Accept",
        "actor": local_actor_uri,
        "object": {
            "type": "Follow",
            "id": follow_id,
            "actor": actor_uri,
            "object": local_actor_uri,
        },
    }

    from app.federation.delivery import DeliveryService, dispatch_new_items

    delivery_svc = DeliveryService(session)
    item = await delivery_svc.deliver_to_inbox(accept_activity, inbox_url)
    dispatch_new_items(
        [item],
        session_factory=ctx.session_factory,
        private_key_pem=ctx.private_key_pem,
        key_id=ctx.key_id,
        semaphore=ctx.semaphore,
    )

    # Create notification.
    notif_repo = NotificationRepository(session)
    notification = Notification(
        type="follow",
        actor_uri=actor_uri,
        actor_name=display_name,
        object_uri=local_actor_uri,
        read=False,
    )
    await notif_repo.add(notification)
    await _emit_notification(notification, ctx)

    logger.info("Accepted Follow from %r", actor_uri)


async def _handle_undo(
    activity: dict[str, Any],
    key_owner_uri: str,
    session: AsyncSession,
) -> None:
    """Handle an incoming ``Undo`` activity.

    Dispatches to the appropriate sub-handler based on the type of the
    wrapped object (``Follow``, ``Like``, or ``Announce``).

    Args:
        activity: The ``Undo`` activity dict.
        key_owner_uri: The verified actor URI.
        session: The current database session.
    """
    inner = activity.get("object")
    if not isinstance(inner, dict):
        logger.debug("Undo.object is not a dict — ignoring")
        return

    inner_type = inner.get("type")
    if inner_type == "Follow":
        await _handle_undo_follow(activity, key_owner_uri, session)
    elif inner_type == "Like":
        await _handle_undo_like(inner, key_owner_uri, session)
    elif inner_type == "Announce":
        await _handle_undo_announce(inner, key_owner_uri, session)
    else:
        logger.debug("Ignoring Undo wrapping unsupported type %r", inner_type)


async def _handle_undo_follow(
    activity: dict[str, Any],
    key_owner_uri: str,
    session: AsyncSession,
) -> None:
    """Handle ``Undo{Follow}``: remove the follower record.

    Args:
        activity: The outer ``Undo`` activity (actor must match key owner).
        key_owner_uri: The verified actor URI.
        session: The current database session.
    """
    actor_uri = activity.get("actor")
    if not isinstance(actor_uri, str) or actor_uri != key_owner_uri:
        logger.warning(
            "Undo{Follow} actor %r does not match key owner %r — ignoring",
            actor_uri,
            key_owner_uri,
        )
        return

    follower_repo = FollowerRepository(session)
    follower = await follower_repo.get_by_actor_uri(actor_uri)
    if follower is not None:
        await follower_repo.delete(follower)
        logger.info("Removed follower %r via Undo{Follow}", actor_uri)
    else:
        logger.debug("Undo{Follow}: no follower record found for %r", actor_uri)


async def _handle_undo_like(
    inner_like: dict[str, Any],
    key_owner_uri: str,
    session: AsyncSession,
) -> None:
    """Handle ``Undo{Like}``: remove the corresponding ``Like`` record.

    Args:
        inner_like: The ``Like`` activity dict embedded in the ``Undo``.
        key_owner_uri: The verified actor URI (must match the like's actor).
        session: The current database session.
    """
    like_actor = inner_like.get("actor")
    if isinstance(like_actor, str) and like_actor != key_owner_uri:
        logger.warning(
            "Undo{Like} inner actor %r does not match key owner %r — ignoring",
            like_actor,
            key_owner_uri,
        )
        return

    like_id = inner_like.get("id")
    if not isinstance(like_id, str):
        logger.debug("Undo{Like}: inner Like has no id — ignoring")
        return

    like_repo = LikeRepository(session)
    like = await like_repo.get_by_activity_uri(like_id)
    if like is not None:
        await like_repo.delete(like)
        logger.info("Removed Like %r via Undo{Like}", like_id)
    else:
        logger.debug("Undo{Like}: no Like record found for activity_uri=%r", like_id)


async def _handle_undo_announce(
    inner_announce: dict[str, Any],
    key_owner_uri: str,
    session: AsyncSession,
) -> None:
    """Handle ``Undo{Announce}``: remove the corresponding timeline entry.

    Finds the timeline item matching the announce's actor, type, and
    boosted-object URI and deletes it.

    Args:
        inner_announce: The ``Announce`` activity dict embedded in the ``Undo``.
        key_owner_uri: The verified actor URI (must match the announce's actor).
        session: The current database session.
    """
    announce_actor = inner_announce.get("actor")
    if isinstance(announce_actor, str) and announce_actor != key_owner_uri:
        logger.warning(
            "Undo{Announce} inner actor %r does not match key owner %r — ignoring",
            announce_actor,
            key_owner_uri,
        )
        return

    object_id = _extract_object_id(inner_announce.get("object"))
    if object_id is None:
        logger.debug("Undo{Announce}: cannot extract object URI — ignoring")
        return

    timeline_repo = TimelineItemRepository(session)
    item = await timeline_repo.get_by_actor_type_and_object_uri(
        actor_uri=key_owner_uri,
        activity_type="Announce",
        original_object_uri=object_id,
    )
    if item is not None:
        await timeline_repo.delete(item)
        logger.info(
            "Removed Announce timeline item for actor=%r object=%r",
            key_owner_uri,
            object_id,
        )
    else:
        logger.debug(
            "Undo{Announce}: no timeline item found for actor=%r object=%r",
            key_owner_uri,
            object_id,
        )


async def _handle_create(
    activity: dict[str, Any],
    key_owner_uri: str,
    session: AsyncSession,
    ctx: InboxContext,
) -> None:
    """Handle ``Create{Note}``.

    Stores the note in the timeline if the actor is a followed account.
    Also creates a ``"reply"`` notification if the note replies to a local
    note.

    Args:
        activity: The ``Create`` activity dict.
        key_owner_uri: The verified actor URI.
        session: The current database session.
        ctx: The current :class:`InboxContext`.
    """
    actor_uri = activity.get("actor")
    if not isinstance(actor_uri, str) or actor_uri != key_owner_uri:
        logger.warning(
            "Create actor %r does not match key owner %r — ignoring",
            actor_uri,
            key_owner_uri,
        )
        return

    obj = activity.get("object")
    if not isinstance(obj, dict):
        logger.debug("Create.object is not a dict — ignoring")
        return

    if obj.get("type") != "Note":
        logger.debug("Create.object.type=%r (not Note) — ignoring", obj.get("type"))
        return

    object_id = obj.get("id")
    if not isinstance(object_id, str):
        logger.debug("Create.object has no id — ignoring")
        return

    # Only store in timeline if we follow this actor.
    following_repo = FollowingRepository(session)
    following = await following_repo.get_by_actor_uri(actor_uri)
    is_followed = following is not None and following.status == "accepted"

    in_reply_to_raw = obj.get("inReplyTo")
    in_reply_to: str | None = None
    if isinstance(in_reply_to_raw, str) and in_reply_to_raw:
        in_reply_to = in_reply_to_raw

    # Check for reply to a local note — always create notification regardless
    # of follow status.
    is_reply_to_local = in_reply_to is not None and _is_local_note_uri(in_reply_to, ctx.domain)

    raw_content_html = obj.get("content")
    content_html: str | None = None
    if isinstance(raw_content_html, str) and raw_content_html:
        content_html = _sanitize_html(raw_content_html)

    # Extract plain-text content from source if available.
    content: str | None = None
    source = obj.get("source")
    if isinstance(source, dict):
        src_content = source.get("content")
        if isinstance(src_content, str) and src_content:
            content = src_content

    # Fetch actor display info from cache.
    from app.services.remote_actor import RemoteActorService

    actor_svc = RemoteActorService(session)
    remote_actor = await actor_svc.get_by_uri(actor_uri)
    actor_name = remote_actor.display_name if remote_actor else None

    if is_followed or is_reply_to_local:
        # Deduplication: skip if already stored.
        timeline_repo = TimelineItemRepository(session)
        existing = await timeline_repo.get_by_object_uri(object_id)
        if existing is None:
            item = TimelineItem(
                activity_type="Create",
                actor_uri=actor_uri,
                actor_name=actor_name,
                content=content,
                content_html=content_html,
                original_object_uri=object_id,
                in_reply_to=in_reply_to,
                raw_activity=json.dumps(activity, ensure_ascii=False),
            )
            timeline_repo._session.add(item)
            await session.flush()

    if is_reply_to_local:
        notif_repo = NotificationRepository(session)
        notification = Notification(
            type="reply",
            actor_uri=actor_uri,
            actor_name=actor_name,
            object_uri=object_id,
            content=content_html,
            read=False,
        )
        await notif_repo.add(notification)
        await _emit_notification(notification, ctx)
        logger.info("Reply notification created from %r on %r", actor_uri, in_reply_to)


async def _handle_announce(
    activity: dict[str, Any],
    key_owner_uri: str,
    session: AsyncSession,
    ctx: InboxContext,
) -> None:
    """Handle an ``Announce`` (boost) activity.

    Stores the item in the timeline if the actor is followed.  If the
    boosted object is one of our local notes, also creates a ``"boost"``
    notification.

    Args:
        activity: The ``Announce`` activity dict.
        key_owner_uri: The verified actor URI.
        session: The current database session.
        ctx: The current :class:`InboxContext`.
    """
    actor_uri = activity.get("actor")
    if not isinstance(actor_uri, str) or actor_uri != key_owner_uri:
        logger.warning(
            "Announce actor %r does not match key owner %r — ignoring",
            actor_uri,
            key_owner_uri,
        )
        return

    object_id = _extract_object_id(activity.get("object"))
    if object_id is None:
        logger.debug("Announce.object has no extractable id — ignoring")
        return

    # Fetch actor info for display.
    from app.services.remote_actor import RemoteActorService

    actor_svc = RemoteActorService(session)
    remote_actor = await actor_svc.get_by_uri(actor_uri)
    actor_name = remote_actor.display_name if remote_actor else None

    # Check if we follow this actor.
    following_repo = FollowingRepository(session)
    following = await following_repo.get_by_actor_uri(actor_uri)
    is_followed = following is not None and following.status == "accepted"

    is_boost_of_local = _is_local_note_uri(object_id, ctx.domain)

    if is_followed:
        # Try to resolve the boosted object's content.
        boosted_obj = activity.get("object")
        content_html: str | None = None
        content: str | None = None

        if isinstance(boosted_obj, dict):
            raw_html = boosted_obj.get("content")
            if isinstance(raw_html, str) and raw_html:
                content_html = _sanitize_html(raw_html)
            source = boosted_obj.get("source")
            if isinstance(source, dict):
                src = source.get("content")
                if isinstance(src, str) and src:
                    content = src
        else:
            # object is just a URI — attempt to fetch it.
            fetched = await _fetch_ap_object(object_id)
            if fetched is not None:
                raw_html = fetched.get("content")
                if isinstance(raw_html, str) and raw_html:
                    content_html = _sanitize_html(raw_html)
                source = fetched.get("source")
                if isinstance(source, dict):
                    src = source.get("content")
                    if isinstance(src, str) and src:
                        content = src

        # Deduplication.
        timeline_repo = TimelineItemRepository(session)
        existing = await timeline_repo.get_by_actor_type_and_object_uri(
            actor_uri=actor_uri,
            activity_type="Announce",
            original_object_uri=object_id,
        )
        if existing is None:
            item = TimelineItem(
                activity_type="Announce",
                actor_uri=actor_uri,
                actor_name=actor_name,
                content=content,
                content_html=content_html,
                original_object_uri=object_id,
                raw_activity=json.dumps(activity, ensure_ascii=False),
            )
            timeline_repo._session.add(item)
            await session.flush()

    if is_boost_of_local:
        notif_repo = NotificationRepository(session)
        notification = Notification(
            type="boost",
            actor_uri=actor_uri,
            actor_name=actor_name,
            object_uri=object_id,
            read=False,
        )
        await notif_repo.add(notification)
        await _emit_notification(notification, ctx)
        logger.info("Boost notification: %r boosted local note %r", actor_uri, object_id)


async def _handle_like(
    activity: dict[str, Any],
    key_owner_uri: str,
    session: AsyncSession,
    ctx: InboxContext,
) -> None:
    """Handle an incoming ``Like`` activity.

    Stores a :class:`~app.models.like.Like` record for deduplication and
    future Undo matching.  If the liked object is a local note, creates a
    ``"like"`` notification.

    Idempotent: duplicate ``Like`` activities (same ``id``) are silently
    ignored.  A pre-flush existence check handles the common case; an
    ``IntegrityError`` catch guards against the rare race where two
    concurrent deliveries of the same activity both pass the existence
    check before either commits.

    Args:
        activity: The ``Like`` activity dict.
        key_owner_uri: The verified actor URI.
        session: The current database session.
        ctx: The current :class:`InboxContext`.
    """
    actor_uri = activity.get("actor")
    if not isinstance(actor_uri, str) or actor_uri != key_owner_uri:
        logger.warning(
            "Like actor %r does not match key owner %r — ignoring",
            actor_uri,
            key_owner_uri,
        )
        return

    activity_id = activity.get("id")
    if not isinstance(activity_id, str):
        logger.debug("Like has no id — ignoring")
        return

    object_id = _extract_object_id(activity.get("object"))
    if object_id is None:
        logger.debug("Like.object has no extractable id — ignoring")
        return

    # Deduplication: skip if this Like activity was already received.
    like_repo = LikeRepository(session)
    existing = await like_repo.get_by_activity_uri(activity_id)
    if existing is not None:
        logger.debug("Duplicate Like activity %r — ignoring", activity_id)
        return

    from sqlalchemy.exc import IntegrityError

    like = Like(
        note_uri=object_id,
        actor_uri=actor_uri,
        activity_uri=activity_id,
    )
    try:
        await like_repo.add(like)
        await session.flush()
    except IntegrityError:
        # Two concurrent deliveries of the same Like both passed the
        # existence check above before either committed — the second one
        # hits the UNIQUE constraint on activity_uri.  Roll back the
        # savepoint and treat this as a duplicate.
        await session.rollback()
        logger.debug("Concurrent duplicate Like activity %r — ignoring", activity_id)
        return

    if _is_local_note_uri(object_id, ctx.domain):
        from app.services.remote_actor import RemoteActorService

        actor_svc = RemoteActorService(session)
        remote_actor = await actor_svc.get_by_uri(actor_uri)
        actor_name = remote_actor.display_name if remote_actor else None

        notif_repo = NotificationRepository(session)
        notification = Notification(
            type="like",
            actor_uri=actor_uri,
            actor_name=actor_name,
            object_uri=object_id,
            read=False,
        )
        await notif_repo.add(notification)
        await _emit_notification(notification, ctx)
        logger.info("Like notification: %r liked local note %r", actor_uri, object_id)


async def _handle_delete(
    activity: dict[str, Any],
    session: AsyncSession,
) -> None:
    """Handle an incoming ``Delete`` activity.

    Removes any timeline items or cached objects whose URI matches the
    deleted object.  Also removes any ``Like`` records for that URI.

    Note: for security, we only check that the request was signed (done
    at the route level); we do not re-verify that the deleting actor
    owns the object, since the content was received from that actor
    and we simply discard it from our local cache.

    Args:
        activity: The ``Delete`` activity dict.
        session: The current database session.
    """
    obj = activity.get("object")
    object_id: str | None = None

    if isinstance(obj, str):
        object_id = obj
    elif isinstance(obj, dict):
        # Tombstone or plain id reference.
        raw = obj.get("id")
        if isinstance(raw, str):
            object_id = raw

    if object_id is None:
        logger.debug("Delete: cannot extract object URI — ignoring")
        return

    # Remove matching timeline items.
    timeline_repo = TimelineItemRepository(session)
    item = await timeline_repo.get_by_object_uri(object_id)
    if item is not None:
        await timeline_repo.delete(item)
        logger.info("Deleted timeline item for object %r", object_id)

    # Remove any Like records for this object.
    like_repo = LikeRepository(session)
    like = await like_repo.get_by_note_uri(object_id)
    if like is not None:
        await like_repo.delete(like)
        logger.debug("Deleted Like record for object %r", object_id)


async def _handle_update(
    activity: dict[str, Any],
    session: AsyncSession,
) -> None:
    """Handle an incoming ``Update`` activity.

    Overwrites the cached content of any timeline item whose
    ``original_object_uri`` matches the updated object's ``id``.

    Args:
        activity: The ``Update`` activity dict.
        session: The current database session.
    """
    obj = activity.get("object")
    if not isinstance(obj, dict):
        logger.debug("Update.object is not a dict — ignoring")
        return

    object_id = obj.get("id")
    if not isinstance(object_id, str):
        logger.debug("Update.object has no id — ignoring")
        return

    timeline_repo = TimelineItemRepository(session)
    item = await timeline_repo.get_by_object_uri(object_id)
    if item is None:
        logger.debug("Update: no timeline item found for %r — ignoring", object_id)
        return

    raw_html = obj.get("content")
    if isinstance(raw_html, str) and raw_html:
        item.content_html = _sanitize_html(raw_html)

    source = obj.get("source")
    if isinstance(source, dict):
        src = source.get("content")
        if isinstance(src, str) and src:
            item.content = src

    if isinstance(raw_html, str) and raw_html:
        item.raw_activity = json.dumps(activity, ensure_ascii=False)
        await session.flush()
        logger.info("Updated cached content for timeline item %r", object_id)


async def _handle_accept(
    activity: dict[str, Any],
    key_owner_uri: str,
    session: AsyncSession,
) -> None:
    """Handle an incoming ``Accept{Follow}`` activity.

    Marks the corresponding outgoing follow request as ``"accepted"``.

    Args:
        activity: The ``Accept`` activity dict.
        key_owner_uri: The verified actor URI (the actor accepting our follow).
        session: The current database session.
    """
    actor_uri = activity.get("actor")
    if not isinstance(actor_uri, str) or actor_uri != key_owner_uri:
        logger.warning(
            "Accept actor %r does not match key owner %r — ignoring",
            actor_uri,
            key_owner_uri,
        )
        return

    following_repo = FollowingRepository(session)
    following = await following_repo.get_by_actor_uri(actor_uri)
    if following is None:
        logger.debug("Accept{Follow}: no Following record found for %r — ignoring", actor_uri)
        return

    following.status = "accepted"
    await session.flush()
    logger.info("Follow accepted by %r", actor_uri)


async def _handle_reject(
    activity: dict[str, Any],
    key_owner_uri: str,
    session: AsyncSession,
) -> None:
    """Handle an incoming ``Reject{Follow}`` activity.

    Removes the corresponding outgoing follow request.

    Args:
        activity: The ``Reject`` activity dict.
        key_owner_uri: The verified actor URI (the actor rejecting our follow).
        session: The current database session.
    """
    actor_uri = activity.get("actor")
    if not isinstance(actor_uri, str) or actor_uri != key_owner_uri:
        logger.warning(
            "Reject actor %r does not match key owner %r — ignoring",
            actor_uri,
            key_owner_uri,
        )
        return

    following_repo = FollowingRepository(session)
    following = await following_repo.get_by_actor_uri(actor_uri)
    if following is not None:
        await following_repo.delete(following)
        logger.info("Follow rejected by %r — removed Following record", actor_uri)
    else:
        logger.debug("Reject{Follow}: no Following record found for %r — ignoring", actor_uri)
