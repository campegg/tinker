"""Activity document builders for the ActivityPub outbox.

Provides pure functions for constructing Note objects and their associated
Create, Update, and Delete activities as JSON-LD dictionaries, ready for
serialisation and federation.

These functions build data structures only — they do not perform any network
or database I/O. Delivery to remote inboxes is handled in WP-09.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

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


def _to_ap_datetime(dt: datetime) -> str:
    """Format a datetime as an ActivityPub-compatible ISO 8601 UTC string.

    Args:
        dt: The datetime to format. Naive datetimes are assumed to be UTC.

    Returns:
        An ISO 8601 string in UTC, ending with ``"Z"``
        (e.g. ``"2024-01-01T12:00:00Z"``).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    published = _to_ap_datetime(note.published_at)
    updated = _to_ap_datetime(note.updated_at)

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
    updated_ts = _to_ap_datetime(note.updated_at)

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
        "published": _to_ap_datetime(datetime.now(UTC)),
        "to": [AP_PUBLIC],
        "object": {
            "id": note_ap_id,
            "type": "Tombstone",
        },
    }
