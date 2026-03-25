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
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from quart import Blueprint, Response, current_app, g, request

from app.admin.auth import get_or_create_csrf_token, require_auth, validate_csrf
from app.federation.delivery import DeliveryService, dispatch_new_items
from app.federation.outbox import (
    build_announce_activity,
    build_create_activity,
    build_delete_activity,
    build_like_activity,
    build_undo_announce_activity,
    build_undo_like_activity,
    build_update_activity,
    generate_activity_id,
)
from app.media import ALLOWED_MIME_TYPES, MAX_FILE_SIZE_BYTES, process_image, save_upload
from app.models.boost import Boost
from app.models.like import Like
from app.models.media_attachment import MediaAttachment
from app.repositories.boost import BoostRepository
from app.repositories.following import FollowingRepository
from app.repositories.like import LikeRepository
from app.repositories.media_attachment import MediaAttachmentRepository
from app.repositories.note import NoteRepository
from app.repositories.remote_actor import RemoteActorRepository
from app.repositories.timeline_item import TimelineItemRepository
from app.services.keypair import KeypairService
from app.services.note import NoteService
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
