"""Shared formatting utilities for ActivityPub values.

Provides canonical formatting functions used across the application for
ActivityPub datetime serialisation and Fediverse handle derivation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse


def format_ap_datetime(dt: datetime) -> str:
    """Format a datetime as an ActivityPub-compatible ISO 8601 UTC string.

    Handles both naive and timezone-aware datetimes. Naive datetimes are
    assumed to be UTC.

    Args:
        dt: The datetime to format.

    Returns:
        An ISO 8601 string in UTC, ending with ``"Z"``
        (e.g. ``"2024-01-01T12:00:00Z"``).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def derive_handle(actor_uri: str) -> str:
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
