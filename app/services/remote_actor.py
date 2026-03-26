"""Remote actor fetching service for ActivityPub federation.

Handles fetching, caching, and refreshing remote ActivityPub actor
documents. Actor metadata is cached locally in the database with a
configurable TTL to avoid redundant network requests while still
keeping public keys and inbox URLs reasonably fresh.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx
import nh3

from app.core.config import USER_AGENT
from app.models.remote_actor import RemoteActor
from app.repositories.remote_actor import RemoteActorRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_ACCEPT_HEADER = "application/activity+json"
_REQUEST_TIMEOUT_SECONDS = 10.0


class RemoteActorService:
    """Service for fetching and caching remote ActivityPub actor documents.

    Wraps :class:`RemoteActorRepository` and ``httpx`` to provide a
    caching layer over remote actor lookups.  Cached entries expire after
    :attr:`CACHE_TTL_SECONDS` seconds, at which point the next lookup
    triggers a background re-fetch.

    Args:
        session: The async database session used for the lifetime of
            this service instance.
    """

    # TTL for cached actor documents (24 hours).
    CACHE_TTL_SECONDS: int = 86400

    def __init__(self, session: AsyncSession) -> None:
        """Initialise the remote actor service.

        Args:
            session: The async database session to use.
        """
        self._repo = RemoteActorRepository(session)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_by_uri(self, uri: str) -> RemoteActor | None:
        """Look up a remote actor, fetching from the network if needed.

        The lookup strategy is:

        1. Check the local cache via the repository.
        2. If found and the cached entry has not expired, return it.
        3. If found but expired, re-fetch from the remote server and
           update the cache.
        4. If not found at all, fetch from the remote server and store.

        Args:
            uri: The canonical ActivityPub URI of the remote actor.

        Returns:
            The cached or freshly-fetched :class:`RemoteActor`, or
            ``None`` if the actor could not be retrieved.
        """
        cached = await self._repo.get_by_uri(uri)
        if cached is not None and not self._is_expired(cached):
            return cached

        # Either not cached or expired - fetch from remote.
        return await self.fetch_and_cache(uri)

    async def fetch_actor_document(self, uri: str) -> dict[str, Any] | None:
        """Fetch a remote actor's JSON-LD document over HTTP.

        Makes an HTTP GET request to *uri* with the ``Accept`` header
        set to ``application/activity+json`` so the remote server
        returns the ActivityPub actor representation.

        Args:
            uri: The URL of the remote actor document.

        Returns:
            The parsed JSON dictionary, or ``None`` if the request
            failed for any reason (network error, non-2xx status, or
            invalid JSON).
        """
        try:
            async with httpx.AsyncClient(
                timeout=_REQUEST_TIMEOUT_SECONDS,
                follow_redirects=True,
            ) as client:
                response = await client.get(
                    uri,
                    headers={
                        "Accept": _ACCEPT_HEADER,
                        "User-Agent": USER_AGENT,
                    },
                )
                response.raise_for_status()
                doc: dict[str, Any] = response.json()
                return doc
        except httpx.HTTPStatusError as exc:
            logger.error(
                "HTTP %s fetching actor document at %s",
                exc.response.status_code,
                uri,
            )
        except httpx.RequestError as exc:
            logger.error(
                "Network error fetching actor document at %s: %s",
                uri,
                exc,
            )
        except Exception:
            logger.exception("Unexpected error fetching actor document at %s", uri)

        return None

    async def fetch_and_cache(self, uri: str) -> RemoteActor | None:
        """Fetch a remote actor document and persist it locally.

        If an entry for *uri* already exists in the cache it is updated
        in place; otherwise a new :class:`RemoteActor` row is created.

        Args:
            uri: The canonical ActivityPub URI of the remote actor.

        Returns:
            The persisted :class:`RemoteActor`, or ``None`` if the
            remote fetch failed or the document was missing required
            fields.
        """
        doc = await self.fetch_actor_document(uri)
        if doc is None:
            return None

        try:
            parsed = self._parse_actor_document(doc)
        except _MissingFieldError as exc:
            logger.error(
                "Remote actor document at %s missing required field: %s",
                uri,
                exc,
            )
            return None

        now = datetime.now(UTC)

        try:
            existing = await self._repo.get_by_uri(uri)

            if existing is not None:
                existing.display_name = parsed["display_name"]
                existing.handle = parsed["handle"]
                existing.bio = parsed["bio"]
                existing.avatar_url = parsed["avatar_url"]
                existing.header_image_url = parsed["header_image_url"]
                existing.inbox_url = parsed["inbox_url"]
                existing.shared_inbox_url = parsed["shared_inbox_url"]
                existing.public_key = parsed["public_key"]
                existing.fetched_at = now
                await self._repo.commit()
                return existing

            actor = RemoteActor(
                uri=parsed["uri"],
                display_name=parsed["display_name"],
                handle=parsed["handle"],
                bio=parsed["bio"],
                avatar_url=parsed["avatar_url"],
                header_image_url=parsed["header_image_url"],
                inbox_url=parsed["inbox_url"],
                shared_inbox_url=parsed["shared_inbox_url"],
                public_key=parsed["public_key"],
                fetched_at=now,
            )
            actor = await self._repo.add(actor)
            await self._repo.commit()
            return actor
        except Exception:
            logger.exception("Failed to persist remote actor %s to the database", uri)
            return None

    async def get_public_key(self, actor_uri: str) -> str | None:
        """Return the PEM-encoded public key for a remote actor.

        Convenience wrapper around :meth:`get_by_uri` that extracts
        only the ``public_key`` field.

        Args:
            actor_uri: The canonical ActivityPub URI of the remote actor.

        Returns:
            The PEM-encoded public key string, or ``None`` if the actor
            could not be retrieved.
        """
        actor = await self.get_by_uri(actor_uri)
        if actor is None:
            return None
        return actor.public_key

    async def refresh(self, actor_uri: str) -> RemoteActor | None:
        """Force re-fetch a remote actor regardless of cache TTL.

        Used by the HTTP Signature verification fallback: when
        verification fails with the cached key, the caller should
        invoke this method to fetch a potentially rotated key and
        retry once.

        Args:
            actor_uri: The canonical ActivityPub URI of the remote actor.

        Returns:
            The refreshed :class:`RemoteActor`, or ``None`` if the
            remote fetch failed.
        """
        return await self.fetch_and_cache(actor_uri)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _is_expired(self, actor: RemoteActor) -> bool:
        """Check whether a cached actor entry has exceeded the TTL.

        Args:
            actor: The cached actor entity to check.

        Returns:
            ``True`` if the entry is stale and should be re-fetched.
        """
        fetched_at = actor.fetched_at
        # Safety measure: SQLite may store datetimes without tzinfo.
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=UTC)

        cutoff = datetime.now(UTC) - timedelta(seconds=self.CACHE_TTL_SECONDS)
        return fetched_at < cutoff

    @staticmethod
    def _parse_actor_document(doc: dict[str, Any]) -> dict[str, Any]:
        """Extract relevant fields from a JSON-LD actor document.

        Args:
            doc: The parsed JSON-LD dictionary from the remote server.

        Returns:
            A dictionary with the keys ``uri``, ``display_name``,
            ``handle``, ``bio``, ``avatar_url``, ``header_image_url``,
            ``inbox_url``, ``shared_inbox_url``, and ``public_key``.

        Raises:
            _MissingFieldError: If a required field (``id``, ``inbox``,
                or ``publicKey.publicKeyPem``) is missing.
        """
        actor_id = doc.get("id")
        if not actor_id or not isinstance(actor_id, str):
            raise _MissingFieldError("id")

        inbox = doc.get("inbox")
        if not inbox or not isinstance(inbox, str):
            raise _MissingFieldError("inbox")

        public_key_obj = doc.get("publicKey")
        if not isinstance(public_key_obj, dict):
            raise _MissingFieldError("publicKey")
        public_key_pem = public_key_obj.get("publicKeyPem")
        if not public_key_pem or not isinstance(public_key_pem, str):
            raise _MissingFieldError("publicKey.publicKeyPem")

        # Optional fields ------------------------------------------------

        preferred_username = doc.get("preferredUsername")
        handle: str | None = None
        if isinstance(preferred_username, str) and preferred_username:
            parsed_url = urlparse(actor_id)
            domain = parsed_url.hostname or ""
            handle = f"{preferred_username}@{domain}"

        display_name: str | None = None
        raw_name = doc.get("name")
        if isinstance(raw_name, str) and raw_name:
            display_name = raw_name

        bio: str | None = None
        raw_summary = doc.get("summary")
        if isinstance(raw_summary, str) and raw_summary:
            # Sanitise with nh3 before storage — the summary field is
            # arbitrary HTML from a remote server and will be rendered as
            # innerHTML in the admin profile modal.
            bio = nh3.clean(raw_summary)

        avatar_url: str | None = None
        icon = doc.get("icon")
        if isinstance(icon, dict):
            icon_url = icon.get("url")
            if isinstance(icon_url, str) and icon_url:
                avatar_url = icon_url

        header_image_url: str | None = None
        image = doc.get("image")
        if isinstance(image, dict):
            image_url = image.get("url")
            if isinstance(image_url, str) and image_url:
                header_image_url = image_url

        shared_inbox_url: str | None = None
        endpoints = doc.get("endpoints")
        if isinstance(endpoints, dict):
            raw_shared = endpoints.get("sharedInbox")
            if isinstance(raw_shared, str) and raw_shared:
                shared_inbox_url = raw_shared

        return {
            "uri": actor_id,
            "display_name": display_name,
            "handle": handle,
            "bio": bio,
            "avatar_url": avatar_url,
            "header_image_url": header_image_url,
            "inbox_url": inbox,
            "shared_inbox_url": shared_inbox_url,
            "public_key": public_key_pem,
        }


class _MissingFieldError(Exception):
    """Raised when a required field is absent from an actor document."""

    def __init__(self, field_name: str) -> None:
        super().__init__(field_name)
        self.field_name = field_name

    def __str__(self) -> str:
        """Return the name of the missing field."""
        return self.field_name
