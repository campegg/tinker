"""Like repository for accessing like activity records.

Provides data access methods for the Like model, including lookup by
note URI and activity URI for deduplication and Undo matching.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

if TYPE_CHECKING:
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
