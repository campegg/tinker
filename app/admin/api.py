"""JSON API endpoints for the admin interface.

Provides auth-protected JSON endpoints consumed by admin Web Components.
All state-changing endpoints (POST, PATCH, DELETE) require a valid
``X-CSRF-Token`` header matching the session's CSRF token.

Endpoint groups:

- **CSRF** (``/admin/api/csrf``): current session CSRF token.
- **Timeline** (``/admin/api/timeline``): merged own notes and received
  activities, with cursor-based pagination and polling support.
- **Notes** (``/admin/api/notes``): create, edit, delete.
- **Media** (``/admin/api/media``): upload image attachments.
- **Likes** (``/admin/api/likes``, ``/admin/api/unlikes``): like/unlike
  remote posts; generates and delivers Like/Undo{Like} activities.
- **Boosts** (``/admin/api/boosts``, ``/admin/api/unboosts``): boost/unboost
  remote posts; generates and delivers Announce/Undo{Announce} activities.
- **Notifications** (``/admin/api/notifications``,
  ``/admin/api/notifications/unread-count``,
  ``/admin/api/notifications/mark-all-read``): notification list,
  unread count, and bulk mark-as-read.
- **Follow/Unfollow** (``/admin/api/follow``, ``/admin/api/unfollow``):
  send Follow and Undo{Follow} activities to remote actors.
- **Profile** (``/admin/api/profile``): read and update local user profile
  settings; PATCH fans out ``Update{Person}`` to all followers.
- **Social graph** (``/admin/api/following``, ``/admin/api/followers``):
  paginated lists of accepted follow relationships; DELETE removes a
  follower (sends ``Reject{Follow}`` when the original Follow activity URI
  is stored).
- **Likes list** (``/admin/api/likes``): paginated list of posts the local
  user has liked, joined with cached timeline content.
- **Search** (``/admin/api/search``): WebFinger lookup for a remote actor
  handle (``@user@domain``).
- **Actor** (``/admin/api/actor``): fetch or refresh a remote actor document
  for the profile modal.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx
from quart import Blueprint, Response, current_app, g, request

from app.admin.auth import get_or_create_csrf_token, require_auth, validate_csrf
from app.federation.actor import build_actor_document
from app.federation.delivery import DeliveryService, dispatch_new_items
from app.federation.follow import send_follow, send_unfollow
from app.federation.outbox import (
    build_announce_activity,
    build_create_activity,
    build_delete_activity,
    build_like_activity,
    build_reject_follow_activity,
    build_undo_announce_activity,
    build_undo_like_activity,
    build_update_activity,
    build_update_person_activity,
    generate_activity_id,
)
from app.media import ALLOWED_MIME_TYPES, MAX_FILE_SIZE_BYTES, process_image, save_upload
from app.models.boost import Boost
from app.models.like import Like
from app.models.media_attachment import MediaAttachment
from app.repositories.boost import BoostRepository
from app.repositories.follower import FollowerRepository
from app.repositories.following import FollowingRepository
from app.repositories.like import LikeRepository
from app.repositories.media_attachment import MediaAttachmentRepository
from app.repositories.note import NoteRepository
from app.repositories.notification import NotificationRepository
from app.repositories.remote_actor import RemoteActorRepository
from app.repositories.timeline_item import TimelineItemRepository
from app.services.keypair import KeypairService
from app.services.note import NoteService
from app.services.remote_actor import RemoteActorService
from app.services.settings import SettingsService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

api = Blueprint("api", __name__, url_prefix="/admin/api")


def _json_response(data: Any, *, status: int = 200) -> Response:
    """Serialise ``data`` to a JSON :class:`~quart.Response`.

    Args:
        data: A JSON-serialisable value.
        status: The HTTP status code. Defaults to 200.

    Returns:
        A ``application/json`` response.
    """
    return Response(
        response=json.dumps(data, ensure_ascii=False),
        status=status,
        content_type="application/json; charset=utf-8",
    )


def _csrf_error() -> Response:
    """Return a 403 JSON response for a CSRF token mismatch."""
    return _json_response({"error": "Invalid or missing CSRF token."}, status=403)


def _validate_csrf_header() -> bool:
    """Check the ``X-CSRF-Token`` request header against the session token.

    Returns:
        ``True`` if the header matches the session CSRF token.
    """
    return validate_csrf(request.headers.get("X-CSRF-Token"))


async def _get_delivery_context() -> tuple[str, str, str, str]:
    """Return the values needed to sign and dispatch deliveries.

    Loads the private key from the keypair service and derives the
    actor URI and key ID from app config.

    Returns:
        A tuple of ``(domain, username, private_key_pem, key_id)``.
    """
    session: AsyncSession = g.db_session
    domain: str = current_app.config["TINKER_DOMAIN"]
    username: str = current_app.config["TINKER_USERNAME"]
    keypair_svc = KeypairService(session)
    private_key_pem = await keypair_svc.get_private_key()
    key_id = f"https://{domain}/{username}#main-key"
    return domain, username, private_key_pem, key_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_actor_uri(payload: Any) -> str | None:
    """Extract and validate the ``actor_uri`` field from a JSON request payload.

    Args:
        payload: The parsed JSON payload (may be ``None`` or non-dict).

    Returns:
        The stripped ``actor_uri`` string, or ``None`` if the field is
        missing, not a string, or blank.
    """
    if not payload or not isinstance(payload.get("actor_uri"), str):
        return None
    stripped = payload["actor_uri"].strip()
    return stripped if stripped else None


def _parse_post_id(payload: Any) -> str | None:
    """Extract and validate the ``post_id`` field from a JSON request payload.

    Args:
        payload: The parsed JSON payload (may be ``None`` or non-dict).

    Returns:
        The stripped ``post_id`` string, or ``None`` if the field is
        missing, not a string, or blank.
    """
    if not payload or not isinstance(payload.get("post_id"), str):
        return None
    stripped = payload["post_id"].strip()
    return stripped if stripped else None


async def _find_inbox_for_post(post_id: str, db: AsyncSession) -> str | None:
    """Look up the inbox URL for the author of a given AP post URI.

    Tries, in order:
    1. The ``TimelineItem`` table (which stores ``actor_uri`` per item)
       to identify the author, then the ``RemoteActor`` cache for the inbox.
    2. The ``Following`` table as a fallback — posts we interact with are
       typically from actors we follow, so their inbox is stored there.

    Args:
        post_id: The ActivityPub URI of the post whose author's inbox is needed.
        db: The current database session.

    Returns:
        The inbox URL string, or ``None`` if the author cannot be resolved.
    """
    timeline_repo = TimelineItemRepository(db)
    item = await timeline_repo.get_by_object_uri(post_id)
    actor_uri = item.actor_uri if item else None
    if not actor_uri:
        return None

    actor_repo = RemoteActorRepository(db)
    cached = await actor_repo.get_by_uri(actor_uri)
    if cached and cached.inbox_url:
        return cached.inbox_url

    following_repo = FollowingRepository(db)
    following = await following_repo.get_by_actor_uri(actor_uri)
    if following:
        return following.inbox_url

    return None


def _to_ap_ts(dt: datetime) -> str:
    """Format a datetime as an ISO 8601 UTC string ending in ``Z``.

    Args:
        dt: A datetime (naive or timezone-aware).

    Returns:
        An ISO 8601 string such as ``"2026-03-21T10:00:00Z"``.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _derive_handle(actor_uri: str) -> str:
    """Derive a ``@user@domain`` handle from an ActivityPub actor URI.

    Parses Mastodon-style URIs (``/users/alice``, ``/u/alice``, ``/@alice``)
    to produce a Fediverse handle. Falls back gracefully for non-standard
    URI patterns.

    Args:
        actor_uri: The canonical ActivityPub URI of the remote actor.

    Returns:
        A handle string such as ``"@alice@mastodon.social"``.
    """
    parsed = urlparse(actor_uri)
    domain = parsed.netloc
    parts = [p for p in parsed.path.split("/") if p and p not in ("users", "u")]
    username = parts[-1].lstrip("@") if parts else "unknown"
    return f"@{username}@{domain}"


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------


@api.route("/csrf", methods=["GET"])
@require_auth
async def get_csrf_token() -> Response:
    """Return the current session CSRF token for use by Web Components.

    Web Components need the CSRF token to include in the ``X-CSRF-Token``
    header on state-changing requests. The token is already embedded in
    ``window.__TINKER__.csrf`` by the HTML shell, but this endpoint is
    available as a fallback for components that need to refresh it.

    Returns:
        ``200`` with ``{"csrf_token": "..."}``
    """
    token = get_or_create_csrf_token()
    return _json_response({"csrf_token": token})


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

_PAGE_SIZE = 20


@api.route("/timeline", methods=["GET"])
@require_auth
async def get_timeline() -> Response:
    """Return a merged, reverse-chronological timeline of own notes and received activities.

    Supports cursor-based pagination (``before`` parameter) and polling
    for new items (``since`` parameter). The two modes are mutually
    exclusive — if both are provided, ``since`` takes precedence.

    Query parameters:

    - ``since`` (ISO 8601, optional): Return only items newer than this
      timestamp. Used by ``<timeline-view>`` for polling.
    - ``before`` (ISO 8601, optional): Return only items older than this
      timestamp. Used for "Load more" pagination.

    Returns:
        ``200`` with ``{"data": [...], "cursor": "...", "has_more": bool}``.
        ``400`` if a timestamp parameter is malformed.
    """
    db: AsyncSession = g.db_session
    domain: str = current_app.config["TINKER_DOMAIN"]
    username: str = current_app.config["TINKER_USERNAME"]
    actor_uri = f"https://{domain}/{username}"
    actor_handle = f"@{username}@{domain}"

    since_str = request.args.get("since")
    before_str = request.args.get("before")
    since_dt: datetime | None = None
    before_dt: datetime | None = None

    if since_str:
        try:
            since_dt = datetime.fromisoformat(since_str.replace("Z", "+00:00"))
        except ValueError:
            return _json_response({"error": "Invalid 'since' timestamp."}, status=400)
    elif before_str:
        try:
            before_dt = datetime.fromisoformat(before_str.replace("Z", "+00:00"))
        except ValueError:
            return _json_response({"error": "Invalid 'before' timestamp."}, status=400)

    note_repo = NoteRepository(db)
    timeline_repo = TimelineItemRepository(db)
    like_repo = LikeRepository(db)
    actor_repo = RemoteActorRepository(db)
    settings_svc = SettingsService(db)

    fetch_limit = _PAGE_SIZE * 2  # over-fetch so merge has enough material

    if since_dt is not None:
        notes = list(await note_repo.get_since_dt(since_dt, fetch_limit))
        tl_items = list(await timeline_repo.get_since_dt(since_dt, fetch_limit))
    elif before_dt is not None:
        notes = list(await note_repo.get_before_dt(before_dt, fetch_limit))
        tl_items = list(await timeline_repo.get_before_dt(before_dt, fetch_limit))
    else:
        notes = list(await note_repo.get_recent(fetch_limit))
        tl_items = list(await timeline_repo.get_recent(fetch_limit))

    liked_uris = await like_repo.get_liked_uris_by_actor(actor_uri)
    display_name = await settings_svc.get_display_name() or username
    raw_avatar = await settings_svc.get_avatar()
    avatar_url = f"/media/{raw_avatar}" if raw_avatar else ""

    # Batch-fetch cached actor data for timeline items to get handles.
    actor_uris = list({item.actor_uri for item in tl_items})
    cached_actors = await actor_repo.get_by_uris(actor_uris)

    unified: list[dict[str, Any]] = []

    for note in notes:
        media_url: str | None = None
        if note.attachments:
            media_url = f"/media/{note.attachments[0].file_path}"
        unified.append(
            {
                "id": str(note.id),
                "type": "own",
                "post_id": note.ap_id,
                "author_name": display_name,
                "author_handle": actor_handle,
                "author_avatar": avatar_url,
                "published": _to_ap_ts(note.published_at),
                "body_html": note.body_html,
                "media_url": media_url,
                "liked": False,
                "reposted": False,
                "own": True,
                "internal_id": str(note.id),
            }
        )

    for item in tl_items:
        cached = cached_actors.get(item.actor_uri)
        handle = cached.handle if cached and cached.handle else _derive_handle(item.actor_uri)
        avatar = item.actor_avatar_url or ""
        name = item.actor_name or item.actor_uri
        uri = item.original_object_uri or ""
        is_liked = uri in liked_uris if uri else False
        unified.append(
            {
                "id": str(item.id),
                "type": item.activity_type.lower(),
                "post_id": uri,
                "author_name": name,
                "author_handle": handle,
                "author_avatar": avatar,
                "published": _to_ap_ts(item.received_at),
                "body_html": item.content_html or "",
                "media_url": None,
                "liked": is_liked,
                "reposted": False,
                "own": False,
                "internal_id": None,
            }
        )

    # Sort by published timestamp descending; string ISO 8601 sorts correctly.
    unified.sort(key=lambda x: x["published"], reverse=True)

    has_more = len(unified) > _PAGE_SIZE
    page = unified[:_PAGE_SIZE]
    cursor = page[-1]["published"] if page else None

    return _json_response({"data": page, "cursor": cursor, "has_more": has_more})


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


@api.route("/notes", methods=["POST"])
@require_auth
async def create_note() -> Response:
    """Create and publish a new note, fanning out delivery to all followers.

    Request body (JSON):

    - ``body`` (str, required): Markdown source of the note.
    - ``in_reply_to`` (str, optional): AP URI of the note being replied to.
    - ``attachment_ids`` (list[str], optional): UUIDs of previously uploaded
      :class:`~app.models.media_attachment.MediaAttachment` records to attach.

    Returns:
        ``201 Created`` with the note's ``id`` and ``ap_id`` on success,
        ``400`` on missing/invalid input, or ``403`` on CSRF failure.
    """
    if not _validate_csrf_header():
        return _csrf_error()

    payload = await request.get_json(silent=True)
    if not payload or not isinstance(payload.get("body"), str) or not payload["body"].strip():
        return _json_response({"error": "Field 'body' is required."}, status=400)

    in_reply_to: str | None = payload.get("in_reply_to")
    if in_reply_to is not None and not isinstance(in_reply_to, str):
        return _json_response({"error": "Field 'in_reply_to' must be a string."}, status=400)

    attachment_ids: list[str] = payload.get("attachment_ids") or []
    if not isinstance(attachment_ids, list):
        return _json_response({"error": "Field 'attachment_ids' must be a list."}, status=400)

    db: AsyncSession = g.db_session
    domain, username, private_key_pem, key_id = await _get_delivery_context()
    actor_uri = f"https://{domain}/{username}"

    note_svc = NoteService(db, domain, username)
    note = await note_svc.create(payload["body"].strip(), in_reply_to=in_reply_to)

    # Link any uploaded attachments to the newly created note.
    if attachment_ids:
        attachment_repo = MediaAttachmentRepository(db)
        for aid_str in attachment_ids:
            try:
                aid = uuid.UUID(aid_str)
            except ValueError:
                continue
            attachment = await attachment_repo.get_by_id(aid)
            if attachment is not None and attachment.note_id is None:
                attachment.note_id = note.id
        await db.commit()
        # Reload note so attachments relationship is populated.
        await db.refresh(note)

    activity = build_create_activity(note, actor_uri)

    delivery_svc = DeliveryService(db)
    items = await delivery_svc.fan_out(activity)

    session_factory = current_app.config["DB_SESSION_FACTORY"]
    semaphore = current_app.config["DELIVERY_SEMAPHORE"]
    dispatch_new_items(
        items,
        session_factory=session_factory,
        private_key_pem=private_key_pem,
        key_id=key_id,
        semaphore=semaphore,
    )

    return _json_response(
        {"id": str(note.id), "ap_id": note.ap_id},
        status=201,
    )


@api.route("/notes/<note_id>", methods=["PATCH"])
@require_auth
async def edit_note(note_id: str) -> Response:
    """Edit a note's body and deliver an Update activity to followers.

    Request body (JSON):

    - ``body`` (str, required): The new Markdown source.

    Args:
        note_id: The UUID string of the note to edit.

    Returns:
        ``200`` with the note's ``id`` and ``ap_id``, ``400`` on invalid
        input, ``403`` on CSRF failure, or ``404`` if the note is not found.
    """
    if not _validate_csrf_header():
        return _csrf_error()

    try:
        note_uuid = uuid.UUID(note_id)
    except ValueError:
        return _json_response({"error": "Invalid note ID."}, status=400)

    payload = await request.get_json(silent=True)
    if not payload or not isinstance(payload.get("body"), str) or not payload["body"].strip():
        return _json_response({"error": "Field 'body' is required."}, status=400)

    session: AsyncSession = g.db_session
    domain, username, private_key_pem, key_id = await _get_delivery_context()
    actor_uri = f"https://{domain}/{username}"

    note_svc = NoteService(session, domain, username)
    note = await note_svc.get_by_id(note_uuid)
    if note is None:
        return _json_response({"error": "Note not found."}, status=404)

    note = await note_svc.edit(note, payload["body"].strip())
    activity = build_update_activity(note, actor_uri)

    delivery_svc = DeliveryService(session)
    items = await delivery_svc.fan_out(activity)

    session_factory = current_app.config["DB_SESSION_FACTORY"]
    semaphore = current_app.config["DELIVERY_SEMAPHORE"]
    dispatch_new_items(
        items,
        session_factory=session_factory,
        private_key_pem=private_key_pem,
        key_id=key_id,
        semaphore=semaphore,
    )

    return _json_response({"id": str(note.id), "ap_id": note.ap_id})


@api.route("/notes/<note_id>", methods=["DELETE"])
@require_auth
async def delete_note(note_id: str) -> Response:
    """Delete a note and deliver a Delete+Tombstone activity to followers.

    Args:
        note_id: The UUID string of the note to delete.

    Returns:
        ``204 No Content`` on success, ``400`` on invalid input,
        ``403`` on CSRF failure, or ``404`` if the note is not found.
    """
    if not _validate_csrf_header():
        return _csrf_error()

    try:
        note_uuid = uuid.UUID(note_id)
    except ValueError:
        return _json_response({"error": "Invalid note ID."}, status=400)

    session: AsyncSession = g.db_session
    domain, username, private_key_pem, key_id = await _get_delivery_context()
    actor_uri = f"https://{domain}/{username}"

    note_svc = NoteService(session, domain, username)
    note = await note_svc.get_by_id(note_uuid)
    if note is None:
        return _json_response({"error": "Note not found."}, status=404)

    note_ap_id = note.ap_id
    await note_svc.delete(note)

    activity = build_delete_activity(note_ap_id, actor_uri)

    delivery_svc = DeliveryService(session)
    items = await delivery_svc.fan_out(activity)

    session_factory = current_app.config["DB_SESSION_FACTORY"]
    semaphore = current_app.config["DELIVERY_SEMAPHORE"]
    dispatch_new_items(
        items,
        session_factory=session_factory,
        private_key_pem=private_key_pem,
        key_id=key_id,
        semaphore=semaphore,
    )

    return Response(response="", status=204)


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------


@api.route("/media", methods=["POST"])
@require_auth
async def upload_media() -> Response:
    """Upload an image file and create a :class:`~app.models.media_attachment.MediaAttachment`.

    Accepts a ``multipart/form-data`` request with:

    - ``file`` (required): the image file.
    - ``alt_text`` (optional): accessibility description string.

    Validates MIME type (JPEG, PNG, WebP, GIF, HEIC/HEIF) and file size
    (max 10 MiB), strips all metadata, optimises, and stores the result.

    Returns:
        ``201 Created`` with ``{"id": "...", "url": "/media/uploads/..."}``
        on success.  ``400`` on validation failure.  ``403`` on CSRF failure.
    """
    if not _validate_csrf_header():
        return _csrf_error()

    files = await request.files
    file = files.get("file")
    if file is None:
        return _json_response({"error": "No file uploaded."}, status=400)

    # MIME type validation from the uploaded Content-Type.
    content_type: str = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_MIME_TYPES:
        return _json_response(
            {
                "error": (
                    f"Unsupported file type {content_type!r}. "
                    f"Allowed: {', '.join(sorted(ALLOWED_MIME_TYPES))}."
                )
            },
            status=400,
        )

    raw: bytes = file.read()
    max_mib = MAX_FILE_SIZE_BYTES // (1024 * 1024)
    if len(raw) > MAX_FILE_SIZE_BYTES:
        return _json_response(
            {"error": f"File too large. Maximum size is {max_mib} MiB."},
            status=400,
        )

    try:
        processed, output_mime = process_image(raw)
    except ValueError as exc:
        return _json_response({"error": str(exc)}, status=400)

    media_path: str = current_app.config["TINKER_MEDIA_PATH"]
    relative_path = save_upload(processed, output_mime, media_path)

    form = await request.form
    alt_text: str | None = form.get("alt_text") or None

    session: AsyncSession = g.db_session
    attachment = MediaAttachment(
        file_path=relative_path,
        mime_type=output_mime,
        alt_text=alt_text,
    )
    repo = MediaAttachmentRepository(session)
    await repo.add(attachment)
    await session.commit()

    return _json_response(
        {
            "id": str(attachment.id),
            "url": f"/media/{relative_path}",
            "mime_type": output_mime,
        },
        status=201,
    )


# ---------------------------------------------------------------------------
# Likes
# ---------------------------------------------------------------------------


@api.route("/likes", methods=["POST"])
@require_auth
async def like_post() -> Response:
    """Like a remote post and deliver a Like activity to the post author.

    Request body (JSON):

    - ``post_id`` (str, required): AP URI of the post to like.

    The operation is idempotent: if the post is already liked, returns
    ``200`` without creating a duplicate record or re-delivering.

    Returns:
        ``200`` with ``{"status": "ok"}`` on success or if already liked.
        ``400`` on missing/invalid input.  ``403`` on CSRF failure.
    """
    if not _validate_csrf_header():
        return _csrf_error()

    payload = await request.get_json(silent=True)
    post_id = _parse_post_id(payload)
    if post_id is None:
        return _json_response({"error": "Field 'post_id' is required."}, status=400)
    db: AsyncSession = g.db_session
    domain, username, private_key_pem, key_id = await _get_delivery_context()
    actor_uri = f"https://{domain}/{username}"

    like_repo = LikeRepository(db)
    if await like_repo.get_by_note_and_actor(post_id, actor_uri):
        return _json_response({"status": "ok"})

    activity_id = generate_activity_id(actor_uri, "like")
    activity = build_like_activity(post_id, actor_uri, activity_id)

    like = Like(note_uri=post_id, actor_uri=actor_uri, activity_uri=activity_id)
    await like_repo.add(like)
    await db.commit()

    inbox_url = await _find_inbox_for_post(post_id, db)
    if inbox_url:
        delivery_svc = DeliveryService(db)
        item = await delivery_svc.deliver_to_inbox(activity, inbox_url)
        session_factory = current_app.config["DB_SESSION_FACTORY"]
        semaphore = current_app.config["DELIVERY_SEMAPHORE"]
        dispatch_new_items(
            [item],
            session_factory=session_factory,
            private_key_pem=private_key_pem,
            key_id=key_id,
            semaphore=semaphore,
        )

    return _json_response({"status": "ok"})


@api.route("/unlikes", methods=["POST"])
@require_auth
async def unlike_post() -> Response:
    """Unlike a previously liked post and deliver an Undo{Like} activity.

    Request body (JSON):

    - ``post_id`` (str, required): AP URI of the post to unlike.

    The operation is idempotent: if the post is not currently liked,
    returns ``200`` without error.

    Returns:
        ``200`` with ``{"status": "ok"}`` on success or if not liked.
        ``400`` on missing/invalid input.  ``403`` on CSRF failure.
    """
    if not _validate_csrf_header():
        return _csrf_error()

    payload = await request.get_json(silent=True)
    post_id = _parse_post_id(payload)
    if post_id is None:
        return _json_response({"error": "Field 'post_id' is required."}, status=400)
    db: AsyncSession = g.db_session
    domain, username, private_key_pem, key_id = await _get_delivery_context()
    actor_uri = f"https://{domain}/{username}"

    like_repo = LikeRepository(db)
    existing = await like_repo.get_by_note_and_actor(post_id, actor_uri)
    if not existing:
        return _json_response({"status": "ok"})

    like_activity_id = existing.activity_uri or generate_activity_id(actor_uri, "like")
    undo_id = generate_activity_id(actor_uri, "undo-like")
    activity = build_undo_like_activity(like_activity_id, post_id, actor_uri, undo_id)

    await like_repo.delete(existing)
    await db.commit()

    inbox_url = await _find_inbox_for_post(post_id, db)
    if inbox_url:
        delivery_svc = DeliveryService(db)
        item = await delivery_svc.deliver_to_inbox(activity, inbox_url)
        session_factory = current_app.config["DB_SESSION_FACTORY"]
        semaphore = current_app.config["DELIVERY_SEMAPHORE"]
        dispatch_new_items(
            [item],
            session_factory=session_factory,
            private_key_pem=private_key_pem,
            key_id=key_id,
            semaphore=semaphore,
        )

    return _json_response({"status": "ok"})


# ---------------------------------------------------------------------------
# Boosts
# ---------------------------------------------------------------------------


@api.route("/boosts", methods=["POST"])
@require_auth
async def boost_post() -> Response:
    """Boost a remote post and deliver an Announce activity.

    Delivers the Announce activity to the post author's inbox and fans
    out to all accepted followers.

    Request body (JSON):

    - ``post_id`` (str, required): AP URI of the post to boost.

    The operation is idempotent: if the post is already boosted, returns
    ``200`` without creating a duplicate record or re-delivering.

    Returns:
        ``200`` with ``{"status": "ok"}`` on success or if already boosted.
        ``400`` on missing/invalid input.  ``403`` on CSRF failure.
    """
    if not _validate_csrf_header():
        return _csrf_error()

    payload = await request.get_json(silent=True)
    post_id = _parse_post_id(payload)
    if post_id is None:
        return _json_response({"error": "Field 'post_id' is required."}, status=400)
    db: AsyncSession = g.db_session
    domain, username, private_key_pem, key_id = await _get_delivery_context()
    actor_uri = f"https://{domain}/{username}"

    boost_repo = BoostRepository(db)
    if await boost_repo.get_by_note_and_actor(post_id, actor_uri):
        return _json_response({"status": "ok"})

    activity_id = generate_activity_id(actor_uri, "boost")
    activity = build_announce_activity(post_id, actor_uri, activity_id)

    boost = Boost(note_uri=post_id, actor_uri=actor_uri, activity_uri=activity_id)
    await boost_repo.add(boost)
    await db.commit()

    session_factory = current_app.config["DB_SESSION_FACTORY"]
    semaphore = current_app.config["DELIVERY_SEMAPHORE"]
    delivery_svc = DeliveryService(db)

    inbox_url = await _find_inbox_for_post(post_id, db)
    if inbox_url:
        item = await delivery_svc.deliver_to_inbox(activity, inbox_url)
        dispatch_new_items(
            [item],
            session_factory=session_factory,
            private_key_pem=private_key_pem,
            key_id=key_id,
            semaphore=semaphore,
        )

    fan_out_items = await delivery_svc.fan_out(activity)
    dispatch_new_items(
        fan_out_items,
        session_factory=session_factory,
        private_key_pem=private_key_pem,
        key_id=key_id,
        semaphore=semaphore,
    )

    return _json_response({"status": "ok"})


@api.route("/unboosts", methods=["POST"])
@require_auth
async def unboost_post() -> Response:
    """Unboost a previously boosted post and deliver an Undo{Announce} activity.

    Delivers the Undo to the post author's inbox and fans out to all
    accepted followers.

    Request body (JSON):

    - ``post_id`` (str, required): AP URI of the post to unboost.

    The operation is idempotent: if the post is not currently boosted,
    returns ``200`` without error.

    Returns:
        ``200`` with ``{"status": "ok"}`` on success or if not boosted.
        ``400`` on missing/invalid input.  ``403`` on CSRF failure.
    """
    if not _validate_csrf_header():
        return _csrf_error()

    payload = await request.get_json(silent=True)
    post_id = _parse_post_id(payload)
    if post_id is None:
        return _json_response({"error": "Field 'post_id' is required."}, status=400)
    db: AsyncSession = g.db_session
    domain, username, private_key_pem, key_id = await _get_delivery_context()
    actor_uri = f"https://{domain}/{username}"

    boost_repo = BoostRepository(db)
    existing = await boost_repo.get_by_note_and_actor(post_id, actor_uri)
    if not existing:
        return _json_response({"status": "ok"})

    announce_activity_id = existing.activity_uri or generate_activity_id(actor_uri, "boost")
    undo_id = generate_activity_id(actor_uri, "undo-boost")
    activity = build_undo_announce_activity(announce_activity_id, post_id, actor_uri, undo_id)

    await boost_repo.delete(existing)
    await db.commit()

    session_factory = current_app.config["DB_SESSION_FACTORY"]
    semaphore = current_app.config["DELIVERY_SEMAPHORE"]
    delivery_svc = DeliveryService(db)

    inbox_url = await _find_inbox_for_post(post_id, db)
    if inbox_url:
        item = await delivery_svc.deliver_to_inbox(activity, inbox_url)
        dispatch_new_items(
            [item],
            session_factory=session_factory,
            private_key_pem=private_key_pem,
            key_id=key_id,
            semaphore=semaphore,
        )

    fan_out_items = await delivery_svc.fan_out(activity)
    dispatch_new_items(
        fan_out_items,
        session_factory=session_factory,
        private_key_pem=private_key_pem,
        key_id=key_id,
        semaphore=semaphore,
    )

    return _json_response({"status": "ok"})


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


@api.route("/notifications", methods=["GET"])
@require_auth
async def get_notifications() -> Response:
    """Return a paginated list of notifications for the admin.

    Supports cursor-based pagination via the ``before`` query parameter.
    For each notification the response includes a cached actor avatar URL
    and an ``is_following`` boolean indicating whether the local user
    currently follows (or has a pending follow for) that actor.

    Query parameters:

    - ``before`` (ISO 8601, optional): Return only notifications created
      before this timestamp.

    Returns:
        ``200`` with ``{"data": [...], "cursor": "...", "has_more": bool}``.
        ``400`` if the ``before`` timestamp is malformed.
    """
    db: AsyncSession = g.db_session

    before_str = request.args.get("before")
    before_dt: datetime | None = None
    if before_str:
        try:
            before_dt = datetime.fromisoformat(before_str.replace("Z", "+00:00"))
        except ValueError:
            return _json_response({"error": "Invalid 'before' timestamp."}, status=400)

    notif_repo = NotificationRepository(db)
    actor_repo = RemoteActorRepository(db)
    following_repo = FollowingRepository(db)

    fetch_limit = _PAGE_SIZE + 1  # over-fetch by 1 to detect has_more

    if before_dt is not None:
        rows = list(await notif_repo.get_before_dt(before_dt, fetch_limit))
    else:
        rows = list(await notif_repo.get_recent(fetch_limit))

    has_more = len(rows) > _PAGE_SIZE
    page = rows[:_PAGE_SIZE]

    # Batch-fetch cached actor data to avoid N+1 avatar lookups.
    actor_uris = list({n.actor_uri for n in page})
    cached_actors = await actor_repo.get_by_uris(actor_uris)

    # Build the set of actor URIs the local user follows (pending + accepted).
    followed_uris = await following_repo.get_followed_actor_uris()

    data: list[dict[str, Any]] = []
    for n in page:
        cached = cached_actors.get(n.actor_uri)
        handle = cached.handle if cached and cached.handle else _derive_handle(n.actor_uri)
        avatar = (cached.avatar_url or "") if cached else ""
        data.append(
            {
                "id": str(n.id),
                "type": n.type,
                "actor_uri": n.actor_uri,
                "actor_name": n.actor_name or "",
                "actor_handle": handle,
                "actor_avatar": avatar,
                "object_uri": n.object_uri or "",
                "content": n.content or "",
                "created_at": _to_ap_ts(n.created_at),
                "is_following": n.actor_uri in followed_uris,
            }
        )

    cursor = data[-1]["created_at"] if data else None
    return _json_response({"data": data, "cursor": cursor, "has_more": has_more})


@api.route("/notifications/unread-count", methods=["GET"])
@require_auth
async def get_unread_notification_count() -> Response:
    """Return the number of unread notifications.

    Returns:
        ``200`` with ``{"count": n}`` where ``n`` is the total number of
        notifications that have not yet been marked as read.
    """
    db: AsyncSession = g.db_session
    count = await NotificationRepository(db).get_unread_count()
    return _json_response({"count": count})


@api.route("/notifications/mark-all-read", methods=["POST"])
@require_auth
async def mark_all_notifications_read() -> Response:
    """Mark all unread notifications as read.

    Returns:
        ``200`` with ``{"status": "ok"}``.  ``403`` on CSRF failure.
    """
    if not _validate_csrf_header():
        return _csrf_error()
    db: AsyncSession = g.db_session
    await NotificationRepository(db).mark_all_read()
    await db.commit()
    return _json_response({"status": "ok"})


# ---------------------------------------------------------------------------
# Follow / Unfollow
# ---------------------------------------------------------------------------


@api.route("/follow", methods=["POST"])
@require_auth
async def follow_actor() -> Response:
    """Send a ``Follow`` activity to a remote actor's inbox.

    Request body (JSON):

    - ``actor_uri`` (str, required): The AP URI of the actor to follow.

    The operation is idempotent: if a follow is already pending or
    accepted, the endpoint returns ``200`` immediately.

    Returns:
        ``200`` with ``{"status": "ok"}`` on success.
        ``400`` on missing/invalid input.  ``403`` on CSRF failure.
    """
    if not _validate_csrf_header():
        return _csrf_error()

    payload = await request.get_json(silent=True)
    target_uri = _parse_actor_uri(payload)
    if target_uri is None:
        return _json_response({"error": "Field 'actor_uri' is required."}, status=400)

    db: AsyncSession = g.db_session
    domain, username, private_key_pem, key_id = await _get_delivery_context()

    await send_follow(
        target_uri,
        session=db,
        session_factory=current_app.config["DB_SESSION_FACTORY"],
        private_key_pem=private_key_pem,
        key_id=key_id,
        semaphore=current_app.config["DELIVERY_SEMAPHORE"],
        domain=domain,
        username=username,
    )
    return _json_response({"status": "ok"})


@api.route("/unfollow", methods=["POST"])
@require_auth
async def unfollow_actor() -> Response:
    """Send an ``Undo{Follow}`` activity to retract a follow.

    Request body (JSON):

    - ``actor_uri`` (str, required): The AP URI of the actor to unfollow.

    The operation is idempotent: if no follow record exists, the endpoint
    returns ``200`` immediately.

    Returns:
        ``200`` with ``{"status": "ok"}`` on success.
        ``400`` on missing/invalid input.  ``403`` on CSRF failure.
    """
    if not _validate_csrf_header():
        return _csrf_error()

    payload = await request.get_json(silent=True)
    target_uri = _parse_actor_uri(payload)
    if target_uri is None:
        return _json_response({"error": "Field 'actor_uri' is required."}, status=400)

    db: AsyncSession = g.db_session
    domain, username, private_key_pem, key_id = await _get_delivery_context()

    await send_unfollow(
        target_uri,
        session=db,
        session_factory=current_app.config["DB_SESSION_FACTORY"],
        private_key_pem=private_key_pem,
        key_id=key_id,
        semaphore=current_app.config["DELIVERY_SEMAPHORE"],
        domain=domain,
        username=username,
    )
    return _json_response({"status": "ok"})


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


@api.route("/profile", methods=["GET"])
@require_auth
async def get_profile() -> Response:
    """Return the local user's current profile settings.

    Returns:
        ``200`` with profile JSON including ``display_name``, ``bio``
        (raw Markdown), ``bio_html`` (rendered), ``avatar_url``,
        ``header_image_url``, and ``links``.
    """
    db: AsyncSession = g.db_session
    domain: str = current_app.config["TINKER_DOMAIN"]
    username: str = current_app.config["TINKER_USERNAME"]
    settings = SettingsService(db)

    display_name = await settings.get_display_name()
    bio = await settings.get_bio()
    avatar = await settings.get_avatar()
    header_image = await settings.get_header_image()
    links = await settings.get_links()

    avatar_url = f"/media/{avatar}" if avatar else ""
    header_image_url = f"/media/{header_image}" if header_image else ""

    return _json_response(
        {
            "display_name": display_name,
            "bio": bio,
            "bio_html": NoteService.render_markdown(bio) if bio else "",
            "avatar_url": avatar_url,
            "header_image_url": header_image_url,
            "links": links,
            "handle": f"@{username}@{domain}",
        }
    )


@api.route("/profile", methods=["PATCH"])
@require_auth
async def update_profile() -> Response:
    """Update the local user's profile settings.

    Request body (JSON, all fields optional):

    - ``display_name`` (str): The new display name.
    - ``bio`` (str): New biography as Markdown source.
    - ``avatar_path`` (str): File path from a prior ``/admin/api/media``
      upload to use as the avatar.
    - ``header_image_path`` (str): File path from a prior upload to use as
      the header/banner image.
    - ``links`` (list[str]): List of external URL strings.

    After saving, fans out an ``Update{Person}`` activity to all accepted
    followers so remote servers can refresh the cached actor document.

    Returns:
        ``200`` with ``{"status": "ok"}`` on success.
        ``400`` on invalid input.  ``403`` on CSRF failure.
    """
    if not _validate_csrf_header():
        return _csrf_error()

    payload = await request.get_json(silent=True)
    if not payload or not isinstance(payload, dict):
        return _json_response({"error": "JSON body required."}, status=400)

    db: AsyncSession = g.db_session
    domain, username, private_key_pem, key_id = await _get_delivery_context()
    actor_uri = f"https://{domain}/{username}"
    settings = SettingsService(db)

    if "display_name" in payload:
        val = payload["display_name"]
        if not isinstance(val, str):
            return _json_response({"error": "'display_name' must be a string."}, status=400)
        await settings.set_display_name(val.strip())

    if "bio" in payload:
        val = payload["bio"]
        if not isinstance(val, str):
            return _json_response({"error": "'bio' must be a string."}, status=400)
        await settings.set_bio(val)

    if "avatar_path" in payload:
        val = payload["avatar_path"]
        if val is not None and not isinstance(val, str):
            return _json_response({"error": "'avatar_path' must be a string or null."}, status=400)
        await settings.set_avatar(val if val else None)

    if "header_image_path" in payload:
        val = payload["header_image_path"]
        if val is not None and not isinstance(val, str):
            return _json_response(
                {"error": "'header_image_path' must be a string or null."}, status=400
            )
        await settings.set_header_image(val if val else None)

    if "links" in payload:
        val = payload["links"]
        if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
            return _json_response({"error": "'links' must be a list of strings."}, status=400)
        await settings.set_links(val)

    # Fan-out Update{Person} to all followers so the fediverse stays in sync.
    actor_doc = await build_actor_document(domain, username, db)
    activity = build_update_person_activity(actor_doc, actor_uri)
    delivery_svc = DeliveryService(db)
    items = await delivery_svc.fan_out(activity)
    session_factory = current_app.config["DB_SESSION_FACTORY"]
    semaphore = current_app.config["DELIVERY_SEMAPHORE"]
    dispatch_new_items(
        items,
        session_factory=session_factory,
        private_key_pem=private_key_pem,
        key_id=key_id,
        semaphore=semaphore,
    )

    return _json_response({"status": "ok"})


# ---------------------------------------------------------------------------
# Social graph — Following
# ---------------------------------------------------------------------------


@api.route("/following", methods=["GET"])
@require_auth
async def list_following() -> Response:
    """Return a paginated list of accepted following relationships.

    Query params:
        before (str, optional): ISO 8601 UTC cursor — return records
            older than this timestamp.

    Returns:
        ``200`` with ``{"data": [...], "cursor": "...", "has_more": bool}``.
        ``400`` if ``before`` cannot be parsed.
    """
    before_raw = request.args.get("before")
    before_dt: datetime | None = None
    if before_raw:
        try:
            before_dt = datetime.fromisoformat(before_raw.replace("Z", "+00:00"))
        except ValueError:
            return _json_response({"error": "Invalid 'before' parameter."}, status=400)

    db: AsyncSession = g.db_session
    following_repo = FollowingRepository(db)

    limit = _PAGE_SIZE + 1
    if before_dt is None:
        page = await following_repo.get_accepted(limit=limit, offset=0)
    else:
        page = await following_repo.get_accepted_before(before=before_dt, limit=limit)

    has_more = len(page) > _PAGE_SIZE
    records = list(page[:_PAGE_SIZE])

    # Batch-lookup RemoteActor for handles.
    actor_uris = {r.actor_uri for r in records}
    remote_repo = RemoteActorRepository(db)
    cached = await remote_repo.get_by_uris(list(actor_uris))

    data = []
    for r in records:
        remote = cached.get(r.actor_uri)
        handle = remote.handle if remote and remote.handle else _derive_handle(r.actor_uri)
        avatar = r.avatar_url or ""
        data.append(
            {
                "actor_uri": r.actor_uri,
                "display_name": r.display_name or r.actor_uri,
                "handle": handle,
                "avatar_url": avatar,
                "created_at": _to_ap_ts(r.created_at),
            }
        )

    cursor: str | None = _to_ap_ts(records[-1].created_at) if records else None
    return _json_response({"data": data, "cursor": cursor, "has_more": has_more})


# ---------------------------------------------------------------------------
# Social graph — Followers
# ---------------------------------------------------------------------------


@api.route("/followers", methods=["GET"])
@require_auth
async def list_followers() -> Response:
    """Return a paginated list of accepted followers.

    Query params:
        before (str, optional): ISO 8601 UTC cursor.

    Returns:
        ``200`` with ``{"data": [...], "cursor": "...", "has_more": bool}``.
        ``400`` if ``before`` cannot be parsed.
    """
    before_raw = request.args.get("before")
    before_dt: datetime | None = None
    if before_raw:
        try:
            before_dt = datetime.fromisoformat(before_raw.replace("Z", "+00:00"))
        except ValueError:
            return _json_response({"error": "Invalid 'before' parameter."}, status=400)

    db: AsyncSession = g.db_session
    follower_repo = FollowerRepository(db)

    limit = _PAGE_SIZE + 1
    if before_dt is None:
        page = await follower_repo.get_accepted(limit=limit, offset=0)
    else:
        page = await follower_repo.get_accepted_before(before=before_dt, limit=limit)

    has_more = len(page) > _PAGE_SIZE
    records = list(page[:_PAGE_SIZE])

    actor_uris = {r.actor_uri for r in records}
    remote_repo = RemoteActorRepository(db)
    cached = await remote_repo.get_by_uris(list(actor_uris))

    data = []
    for r in records:
        remote = cached.get(r.actor_uri)
        handle = remote.handle if remote and remote.handle else _derive_handle(r.actor_uri)
        avatar = r.avatar_url or ""
        data.append(
            {
                "actor_uri": r.actor_uri,
                "display_name": r.display_name or r.actor_uri,
                "handle": handle,
                "avatar_url": avatar,
                "created_at": _to_ap_ts(r.created_at),
            }
        )

    cursor: str | None = _to_ap_ts(records[-1].created_at) if records else None
    return _json_response({"data": data, "cursor": cursor, "has_more": has_more})


@api.route("/followers", methods=["DELETE"])
@require_auth
async def remove_follower() -> Response:
    """Remove a follower and optionally send ``Reject{Follow}``.

    Request body (JSON):

    - ``actor_uri`` (str, required): The AP URI of the follower to remove.

    If the follower record has a ``follow_activity_uri``, a
    ``Reject{Follow}`` activity is delivered to their inbox (best-effort,
    fire-and-forget) before the record is deleted.  Legacy records without
    a stored activity URI are deleted silently.

    Returns:
        ``200`` with ``{"status": "ok"}`` on success.
        ``400`` on missing input.  ``403`` on CSRF failure.
        ``404`` if the follower is not found.
    """
    if not _validate_csrf_header():
        return _csrf_error()

    payload = await request.get_json(silent=True)
    target_uri = _parse_actor_uri(payload)
    if target_uri is None:
        return _json_response({"error": "Field 'actor_uri' is required."}, status=400)

    db: AsyncSession = g.db_session
    follower_repo = FollowerRepository(db)
    follower = await follower_repo.get_by_actor_uri(target_uri)
    if follower is None:
        return _json_response({"error": "Follower not found."}, status=404)

    if follower.follow_activity_uri:
        domain, username, private_key_pem, key_id = await _get_delivery_context()
        actor_uri = f"https://{domain}/{username}"
        activity = build_reject_follow_activity(follower.follow_activity_uri, actor_uri)
        delivery_svc = DeliveryService(db)
        inbox = follower.inbox_url
        item = await delivery_svc.deliver_to_inbox(activity, inbox)
        session_factory = current_app.config["DB_SESSION_FACTORY"]
        semaphore = current_app.config["DELIVERY_SEMAPHORE"]
        dispatch_new_items(
            [item],
            session_factory=session_factory,
            private_key_pem=private_key_pem,
            key_id=key_id,
            semaphore=semaphore,
        )

    await follower_repo.delete(follower)
    await db.commit()
    return _json_response({"status": "ok"})


# ---------------------------------------------------------------------------
# Likes list
# ---------------------------------------------------------------------------


@api.route("/likes", methods=["GET"])
@require_auth
async def list_likes() -> Response:
    """Return a paginated list of posts the local user has liked.

    Each result item is joined with cached timeline content; liked posts
    with no local cache entry are silently skipped.

    Query params:
        before (str, optional): ISO 8601 UTC cursor.

    Returns:
        ``200`` with ``{"data": [...], "cursor": "...", "has_more": bool}``.
        ``400`` if ``before`` cannot be parsed.
    """
    before_raw = request.args.get("before")
    before_dt: datetime | None = None
    if before_raw:
        try:
            before_dt = datetime.fromisoformat(before_raw.replace("Z", "+00:00"))
        except ValueError:
            return _json_response({"error": "Invalid 'before' parameter."}, status=400)

    db: AsyncSession = g.db_session
    domain: str = current_app.config["TINKER_DOMAIN"]
    username: str = current_app.config["TINKER_USERNAME"]
    actor_uri = f"https://{domain}/{username}"

    like_repo = LikeRepository(db)
    limit = _PAGE_SIZE + 1
    likes = await like_repo.get_recent_by_local_actor(actor_uri, limit=limit, before=before_dt)

    has_more = len(likes) > _PAGE_SIZE
    likes = list(likes[:_PAGE_SIZE])

    # Batch-lookup timeline items for the liked post URIs.
    note_uris = {lk.note_uri for lk in likes}
    tl_repo = TimelineItemRepository(db)
    tl_map = await tl_repo.get_by_object_uris(note_uris)

    # Batch-lookup actor handles.
    present_actor_uris = {tl_map[lk.note_uri].actor_uri for lk in likes if lk.note_uri in tl_map}
    remote_repo = RemoteActorRepository(db)
    cached_actors = await remote_repo.get_by_uris(list(present_actor_uris))

    data = []
    for lk in likes:
        item = tl_map.get(lk.note_uri)
        if item is None:
            # No cached content — skip.
            continue
        cached = cached_actors.get(item.actor_uri)
        handle = cached.handle if cached and cached.handle else _derive_handle(item.actor_uri)
        avatar = item.actor_avatar_url or ""
        name = item.actor_name or item.actor_uri
        data.append(
            {
                "id": str(item.id),
                "post_id": item.original_object_uri or "",
                "author_name": name,
                "author_handle": handle,
                "author_avatar": avatar,
                "published": _to_ap_ts(item.received_at),
                "body_html": item.content_html or "",
                "media_url": None,
                "liked": True,
                "reposted": False,
            }
        )

    cursor: str | None = _to_ap_ts(likes[-1].created_at) if likes else None
    return _json_response({"data": data, "cursor": cursor, "has_more": has_more})


# ---------------------------------------------------------------------------
# Search — remote actor lookup via WebFinger
# ---------------------------------------------------------------------------

_WEBFINGER_TIMEOUT = 10.0
_USER_AGENT = "Tinker/0.1.0"


def _parse_fediverse_handle(q: str) -> tuple[str, str] | None:
    """Parse a Fediverse handle into (username, domain).

    Accepts ``@user@domain`` or ``user@domain``.

    Args:
        q: The raw handle string from the query parameter.

    Returns:
        A ``(username, domain)`` tuple, or ``None`` if the handle cannot
        be parsed.
    """
    q = q.strip().lstrip("@")
    if "@" not in q:
        return None
    parts = q.split("@", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


@api.route("/search", methods=["GET"])
@require_auth
async def search_actor() -> Response:
    """Look up a remote actor by Fediverse handle via WebFinger.

    Query params:
        q (str, required): The handle to look up, e.g. ``@alice@example.com``
            or ``alice@example.com``.

    Returns:
        ``200`` with actor JSON on success.
        ``400`` on missing or unparseable handle.
        ``404`` if the actor cannot be found via WebFinger.
    """
    q = request.args.get("q", "").strip()
    if not q:
        return _json_response({"error": "Query parameter 'q' is required."}, status=400)

    parsed = _parse_fediverse_handle(q)
    if parsed is None:
        return _json_response(
            {"error": "Invalid handle — expected @user@domain format."}, status=400
        )
    handle_user, handle_domain = parsed

    # 1. WebFinger lookup to resolve the handle to an actor URI.
    webfinger_url = (
        f"https://{handle_domain}/.well-known/webfinger"
        f"?resource=acct:{handle_user}@{handle_domain}"
    )
    try:
        async with httpx.AsyncClient(
            timeout=_WEBFINGER_TIMEOUT,
            follow_redirects=True,
        ) as client:
            resp = await client.get(
                webfinger_url,
                headers={"Accept": "application/jrd+json", "User-Agent": _USER_AGENT},
            )
            resp.raise_for_status()
            wf_data: dict[str, Any] = resp.json()
    except (httpx.HTTPStatusError, httpx.RequestError, ValueError):
        return _json_response({"error": "Actor not found."}, status=404)

    # Extract the AP actor URI from the WebFinger links.
    actor_uri_wf: str | None = None
    for link in wf_data.get("links", []):
        if (
            isinstance(link, dict)
            and link.get("rel") == "self"
            and link.get("type") == "application/activity+json"
        ):
            href = link.get("href")
            if isinstance(href, str) and href:
                actor_uri_wf = href
                break

    if actor_uri_wf is None:
        return _json_response({"error": "Actor not found."}, status=404)

    # 2. Fetch and cache the actor document.
    db: AsyncSession = g.db_session
    actor_svc = RemoteActorService(db)
    actor = await actor_svc.get_by_uri(actor_uri_wf)
    if actor is None:
        return _json_response({"error": "Actor not found."}, status=404)

    following_repo = FollowingRepository(db)
    following = await following_repo.get_by_actor_uri(actor.uri)
    is_following = following is not None and following.status in ("pending", "accepted")

    return _json_response(
        {
            "uri": actor.uri,
            "display_name": actor.display_name or actor.uri,
            "handle": actor.handle or _derive_handle(actor.uri),
            "avatar_url": actor.avatar_url or "",
            "header_image_url": actor.header_image_url or "",
            "bio": actor.bio or "",
            "is_following": is_following,
        }
    )


# ---------------------------------------------------------------------------
# Actor detail — for the remote actor profile modal
# ---------------------------------------------------------------------------


@api.route("/actor", methods=["GET"])
@require_auth
async def get_actor() -> Response:
    """Fetch or refresh a remote actor document for the profile modal.

    Query params:
        uri (str, required): The canonical AP URI of the remote actor.

    Returns:
        ``200`` with actor JSON on success.
        ``400`` on missing URI.
        ``404`` if the actor cannot be fetched.
    """
    uri = request.args.get("uri", "").strip()
    if not uri:
        return _json_response({"error": "Query parameter 'uri' is required."}, status=400)

    db: AsyncSession = g.db_session
    actor_svc = RemoteActorService(db)
    actor = await actor_svc.get_by_uri(uri)
    if actor is None:
        return _json_response({"error": "Actor not found."}, status=404)

    following_repo = FollowingRepository(db)
    following = await following_repo.get_by_actor_uri(actor.uri)
    is_following = following is not None and following.status in ("pending", "accepted")

    return _json_response(
        {
            "uri": actor.uri,
            "display_name": actor.display_name or actor.uri,
            "handle": actor.handle or _derive_handle(actor.uri),
            "avatar_url": actor.avatar_url or "",
            "header_image_url": actor.header_image_url or "",
            "bio": actor.bio or "",
            "is_following": is_following,
        }
    )
