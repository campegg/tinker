"""JSON API endpoints for the admin interface.

Provides auth-protected JSON endpoints consumed by admin Web Components.
All state-changing endpoints (POST, PATCH, DELETE) require a valid
``X-CSRF-Token`` header matching the session's CSRF token.

Note endpoints are the first set of routes; additional endpoint groups
will be added in later work packages (WP-13+).
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from quart import Blueprint, Response, current_app, g, request

from app.admin.auth import require_auth, validate_csrf
from app.federation.delivery import DeliveryService, dispatch_new_items
from app.federation.outbox import (
    build_create_activity,
    build_delete_activity,
    build_update_activity,
)
from app.services.keypair import KeypairService
from app.services.note import NoteService

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
# Notes
# ---------------------------------------------------------------------------


@api.route("/notes", methods=["POST"])
@require_auth
async def create_note() -> Response:
    """Create and publish a new note, fanning out delivery to all followers.

    Request body (JSON):

    - ``body`` (str, required): Markdown source of the note.
    - ``in_reply_to`` (str, optional): AP URI of the note being replied to.

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

    session: AsyncSession = g.db_session
    domain, username, private_key_pem, key_id = await _get_delivery_context()
    actor_uri = f"https://{domain}/{username}"

    note_svc = NoteService(session, domain, username)
    note = await note_svc.create(payload["body"].strip(), in_reply_to=in_reply_to)

    activity = build_create_activity(note, actor_uri)

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
