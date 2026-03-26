"""Like repository for accessing like activity records.

Provides data access methods for the Like model, including lookup by
note URI and activity URI for deduplication and Undo matching.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

from app.models.like import Like
from app.repositories.base import BaseRepository


class LikeRepository(BaseRepository[Like]):
    """Repository for Like entities.

    Extends :class:`BaseRepository` with like-specific queries such as
    lookup by note URI and by activity URI for deduplication and Undo
    handling.

    Args:
        session: The async database session to use for queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialise the repository with a database session.

        Args:
            session: The async database session to use for queries.
        """
        super().__init__(session, Like)

    async def get_by_note_and_actor(self, note_uri: str, actor_uri: str) -> Like | None:
        """Fetch a like by note URI and actor URI.

        Used for idempotency checks: returns the existing like record if
        the given actor has already liked this note.

        Args:
            note_uri: The ActivityPub URI of the liked note.
            actor_uri: The ActivityPub URI of the actor who liked.

        Returns:
            The matching like, or ``None`` if no like exists for the
            given note and actor combination.
        """
        result = await self._session.execute(
            select(Like).where(Like.note_uri == note_uri, Like.actor_uri == actor_uri)
        )
        return result.scalars().first()

    async def get_by_note_uri(self, note_uri: str) -> Like | None:
        """Fetch a like by the URI of the note that was liked.

        If multiple likes exist for the same note URI, the first match
        is returned. This is useful for checking whether a particular
        note has already been liked.

        Args:
            note_uri: The ActivityPub URI of the liked note.

        Returns:
            The matching like, or ``None`` if no like exists for the
            given note URI.
        """
        result = await self._session.execute(select(Like).where(Like.note_uri == note_uri))
        return result.scalars().first()

    async def get_liked_uris_by_actor(self, actor_uri: str) -> set[str]:
        """Return the set of note URIs liked by the given actor.

        Used to annotate timeline items with whether the local user has
        already liked each post, so the UI can render the like button in
        the correct state.

        Args:
            actor_uri: The ActivityPub URI of the actor whose likes to fetch.

        Returns:
            A set of note URI strings that the actor has liked.
        """
        result = await self._session.execute(
            select(Like.note_uri).where(Like.actor_uri == actor_uri)
        )
        rows: Sequence[str] = result.scalars().all()
        return set(rows)

    async def get_recent_by_local_actor(
        self,
        actor_uri: str,
        limit: int,
        before: datetime | None = None,
    ) -> Sequence[Like]:
        """Fetch paginated likes made by the local actor.

        Used to build the Likes view: returns Like records for the given
        actor ordered newest-first, with optional cursor-based pagination.

        Args:
            actor_uri: The AP URI of the local actor whose likes to fetch.
            limit: Maximum number of records to return.
            before: If provided, only return likes with ``created_at``
                strictly before this timestamp (cursor pagination).

        Returns:
            A sequence of Like records ordered from newest to oldest.
        """
        stmt = select(Like).where(Like.actor_uri == actor_uri)
        if before is not None:
            stmt = stmt.where(Like.created_at < before)
        stmt = stmt.order_by(Like.created_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_by_activity_uri(self, activity_uri: str) -> Like | None:
        """Fetch a like by its ActivityPub activity URI.

        The activity URI uniquely identifies the Like activity itself,
        and is used for deduplication on receipt and for matching
        incoming ``Undo{Like}`` activities to the original like.

        Args:
            activity_uri: The ActivityPub URI of the Like activity.

        Returns:
            The matching like, or ``None`` if no like exists with the
            given activity URI.
        """
        result = await self._session.execute(select(Like).where(Like.activity_uri == activity_uri))
        return result.scalars().first()
