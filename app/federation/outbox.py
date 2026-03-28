"""Activity document builders for the ActivityPub outbox.

Provides pure functions for constructing Note objects and their associated
Create, Update, and Delete activities as JSON-LD dictionaries, ready for
serialisation and federation.

These functions build data structures only — they do not perform any network
or database I/O. Delivery to remote inboxes is handled in WP-09.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from app.core.formatting import format_ap_datetime

if TYPE_CHECKING:
    from app.models.note import Note

# The ActivityPub public addressing URI — activities addressed to this URI
# are visible to anyone (i.e. public posts).
AP_PUBLIC = "https://www.w3.org/ns/activitystreams#Public"

# The JSON-LD context array for all top-level AP objects and activities.
# Both namespaces are required: activitystreams for core AS2 terms and
# security/v1 for publicKey and related cryptographic vocabulary.
AP_CONTEXT: list[str] = [
    "https://www.w3.org/ns/activitystreams",
    "https://w3id.org/security/v1",
]

# Matches hashtags in Markdown source text. Requires the "#" to not be
# preceded by a word character so that mid-word occurrences are not matched
# (e.g. "C#" is not a hashtag but "#rust" is).
_HASHTAG_RE = re.compile(r"(?<!\w)#(\w+)", re.UNICODE)


def _extract_tags(body: str) -> list[str]:
    """Extract unique lowercase hashtag names from Markdown source.

    Args:
        body: The raw Markdown source of a note.

    Returns:
        A deduplicated, insertion-order-preserving list of tag names
        without the ``"#"`` prefix (e.g. ``["python", "activitypub"]``).
    """
    seen: dict[str, None] = {}
    for match in _HASHTAG_RE.finditer(body):
        seen[match.group(1).lower()] = None
    return list(seen)


def build_note_object(note: Note, actor_uri: str) -> dict[str, Any]:
    """Build the JSON-LD Note object for a locally authored note.

    The returned dictionary does not include ``"@context"`` — it is
    designed for embedding inside an activity. When serving the Note at
    its canonical URL as a standalone document, callers should add
    ``"@context": AP_CONTEXT`` before serialising.

    Args:
        note: The locally authored note to serialise.
        actor_uri: The canonical AP actor URI of the local user
            (e.g. ``"https://example.com/username"``).

    Returns:
        A dictionary representing the AP Note object.
    """
    domain = urlparse(actor_uri).netloc
    followers_url = f"{actor_uri}/followers"
    published = format_ap_datetime(note.published_at)
    updated = format_ap_datetime(note.updated_at)

    tag_names = _extract_tags(note.body)
    tags: list[dict[str, str]] = [
        {
            "type": "Hashtag",
            "href": f"https://{domain}/tags/{name}",
            "name": f"#{name}",
        }
        for name in tag_names
    ]

    obj: dict[str, Any] = {
        "id": note.ap_id,
        "type": "Note",
        "published": published,
        "attributedTo": actor_uri,
        "content": note.body_html,
        "source": {
            "content": note.body,
            "mediaType": "text/markdown",
        },
        "to": [AP_PUBLIC],
        "cc": [followers_url],
        "sensitive": False,
        "tag": tags,
        "attachment": [
            {
                "type": "Document",
                "mediaType": a.mime_type,
                "url": f"https://{domain}/media/{a.file_path}",
                **({"name": a.alt_text} if a.alt_text else {}),
            }
            for a in note.attachments
        ],
    }

    if note.in_reply_to is not None:
        obj["inReplyTo"] = note.in_reply_to

    # Include "updated" only when the note has been edited after publication.
    # Normalise to UTC before comparing to handle naive datetimes from SQLite.
    pub_dt = note.published_at
    upd_dt = note.updated_at
    if pub_dt.tzinfo is None:
        pub_dt = pub_dt.replace(tzinfo=UTC)
    if upd_dt.tzinfo is None:
        upd_dt = upd_dt.replace(tzinfo=UTC)
    if upd_dt > pub_dt:
        obj["updated"] = updated

    return obj


def build_create_activity(note: Note, actor_uri: str) -> dict[str, Any]:
    """Build a ``Create{Note}`` activity for a locally authored note.

    Args:
        note: The note being published.
        actor_uri: The canonical AP actor URI of the local user.

    Returns:
        A dictionary representing the ``Create`` activity with the Note
        object embedded.
    """
    note_doc = build_note_object(note, actor_uri)
    followers_url = f"{actor_uri}/followers"

    return {
        "@context": AP_CONTEXT,
        "id": f"{note.ap_id}/activity",
        "type": "Create",
        "actor": actor_uri,
        "published": note_doc["published"],
        "to": [AP_PUBLIC],
        "cc": [followers_url],
        "object": note_doc,
    }


def build_update_activity(note: Note, actor_uri: str) -> dict[str, Any]:
    """Build an ``Update{Note}`` activity for an edited note.

    Args:
        note: The updated note (``updated_at`` should reflect the edit time).
        actor_uri: The canonical AP actor URI of the local user.

    Returns:
        A dictionary representing the ``Update`` activity with the revised
        Note object embedded.
    """
    note_doc = build_note_object(note, actor_uri)
    followers_url = f"{actor_uri}/followers"
    updated_ts = format_ap_datetime(note.updated_at)

    return {
        "@context": AP_CONTEXT,
        "id": f"{note.ap_id}#updates/{updated_ts}",
        "type": "Update",
        "actor": actor_uri,
        "published": updated_ts,
        "to": [AP_PUBLIC],
        "cc": [followers_url],
        "object": note_doc,
    }


def build_like_activity(post_uri: str, actor_uri: str, activity_id: str) -> dict[str, Any]:
    """Build a ``Like`` activity for a remote post.

    Args:
        post_uri: The AP URI of the post being liked.
        actor_uri: The canonical AP actor URI of the local user.
        activity_id: The unique AP URI to use as this activity's ``id``.

    Returns:
        A dictionary representing the ``Like`` activity.
    """
    return {
        "@context": AP_CONTEXT,
        "id": activity_id,
        "type": "Like",
        "actor": actor_uri,
        "published": format_ap_datetime(datetime.now(UTC)),
        "object": post_uri,
    }


def build_undo_like_activity(
    like_activity_id: str,
    post_uri: str,
    actor_uri: str,
    activity_id: str,
) -> dict[str, Any]:
    """Build an ``Undo{Like}`` activity to retract a previously sent Like.

    Args:
        like_activity_id: The AP URI of the original ``Like`` activity.
        post_uri: The AP URI of the post that was liked.
        actor_uri: The canonical AP actor URI of the local user.
        activity_id: The unique AP URI to use as this Undo activity's ``id``.

    Returns:
        A dictionary representing the ``Undo{Like}`` activity.
    """
    return {
        "@context": AP_CONTEXT,
        "id": activity_id,
        "type": "Undo",
        "actor": actor_uri,
        "published": format_ap_datetime(datetime.now(UTC)),
        "object": {
            "id": like_activity_id,
            "type": "Like",
            "actor": actor_uri,
            "object": post_uri,
        },
    }


def build_announce_activity(post_uri: str, actor_uri: str, activity_id: str) -> dict[str, Any]:
    """Build an ``Announce`` activity to boost a remote post.

    Args:
        post_uri: The AP URI of the post being boosted.
        actor_uri: The canonical AP actor URI of the local user.
        activity_id: The unique AP URI to use as this activity's ``id``.

    Returns:
        A dictionary representing the ``Announce`` activity.
    """
    followers_url = f"{actor_uri}/followers"
    return {
        "@context": AP_CONTEXT,
        "id": activity_id,
        "type": "Announce",
        "actor": actor_uri,
        "published": format_ap_datetime(datetime.now(UTC)),
        "to": [AP_PUBLIC],
        "cc": [followers_url],
        "object": post_uri,
    }


def build_undo_announce_activity(
    announce_activity_id: str,
    post_uri: str,
    actor_uri: str,
    activity_id: str,
) -> dict[str, Any]:
    """Build an ``Undo{Announce}`` activity to retract a boost.

    Args:
        announce_activity_id: The AP URI of the original ``Announce`` activity.
        post_uri: The AP URI of the post that was boosted.
        actor_uri: The canonical AP actor URI of the local user.
        activity_id: The unique AP URI to use as this Undo activity's ``id``.

    Returns:
        A dictionary representing the ``Undo{Announce}`` activity.
    """
    return {
        "@context": AP_CONTEXT,
        "id": activity_id,
        "type": "Undo",
        "actor": actor_uri,
        "published": format_ap_datetime(datetime.now(UTC)),
        "object": {
            "id": announce_activity_id,
            "type": "Announce",
            "actor": actor_uri,
            "object": post_uri,
        },
    }


def generate_activity_id(actor_uri: str, kind: str) -> str:
    """Generate a unique activity URI for a transient outgoing activity.

    Uses a UUID fragment to ensure uniqueness while keeping the URI
    anchored to the local actor.

    Args:
        actor_uri: The canonical AP actor URI of the local user.
        kind: A short label for the activity type (e.g. ``"like"``,
            ``"boost"``). Used only to make the URI human-readable.

    Returns:
        A URI string suitable for use as an activity ``id``.
    """
    return f"{actor_uri}#{kind}-{uuid.uuid4().hex}"


def build_update_person_activity(
    actor_doc: dict[str, Any],
    actor_uri: str,
) -> dict[str, Any]:
    """Build an ``Update{Person}`` activity to propagate profile changes.

    Used when the local user updates their display name, bio, or avatar so
    that followers' servers can refresh the cached actor document.

    Args:
        actor_doc: The full JSON-LD actor document (as built by
            :func:`app.federation.actor.build_actor_document`).
        actor_uri: The canonical AP actor URI of the local user.

    Returns:
        A dictionary representing the ``Update`` activity with the actor
        document embedded as the ``object`` (``@context`` stripped from
        the embedded object per AP convention).
    """
    now = format_ap_datetime(datetime.now(UTC))
    activity_id = generate_activity_id(actor_uri, "update-person")
    # Strip @context from the embedded object — only the top-level activity
    # carries the context.
    actor_obj = {k: v for k, v in actor_doc.items() if k != "@context"}
    followers_url = f"{actor_uri}/followers"
    return {
        "@context": AP_CONTEXT,
        "id": activity_id,
        "type": "Update",
        "actor": actor_uri,
        "published": now,
        "to": [AP_PUBLIC],
        "cc": [followers_url],
        "object": actor_obj,
    }


def build_reject_follow_activity(
    follow_activity_uri: str,
    actor_uri: str,
) -> dict[str, Any]:
    """Build a ``Reject{Follow}`` activity to terminate a follower relationship.

    Sent when the local user removes a follower.  The remote server will
    remove the follow relationship on its end when it receives this.

    Args:
        follow_activity_uri: The AP ``id`` of the original ``Follow`` activity
            being rejected.
        actor_uri: The canonical AP actor URI of the local user (the rejecter).

    Returns:
        A dictionary representing the ``Reject`` activity.
    """
    activity_id = generate_activity_id(actor_uri, "reject")
    return {
        "@context": AP_CONTEXT,
        "id": activity_id,
        "type": "Reject",
        "actor": actor_uri,
        "published": format_ap_datetime(datetime.now(UTC)),
        "object": follow_activity_uri,
    }


def build_delete_activity(note_ap_id: str, actor_uri: str) -> dict[str, Any]:
    """Build a ``Delete`` activity with a ``Tombstone`` for a deleted note.

    Per the ActivityPub spec, deleted objects are replaced by a Tombstone
    so that remote servers can distinguish "this object no longer exists"
    from "this object was never here".

    Args:
        note_ap_id: The AP URI of the note being deleted.
        actor_uri: The canonical AP actor URI of the local user.

    Returns:
        A dictionary representing the ``Delete`` activity.
    """
    return {
        "@context": AP_CONTEXT,
        "id": f"{note_ap_id}#delete",
        "type": "Delete",
        "actor": actor_uri,
        "published": format_ap_datetime(datetime.now(UTC)),
        "to": [AP_PUBLIC],
        "object": {
            "id": note_ap_id,
            "type": "Tombstone",
        },
    }
