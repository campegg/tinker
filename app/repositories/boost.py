"""Boost repository for accessing outgoing Announce activity records.

Provides data access methods for the Boost model, including lookup by
note URI and activity URI for deduplication and Undo matching.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from app.models.boost import Boost
from app.repositories.base import BaseRepository


class BoostRepository(BaseRepository[Boost]):
    """Repository for Boost entities.

    Extends :class:`BaseRepository` with boost-specific queries such as
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
        super().__init__(session, Boost)

    async def get_by_note_and_actor(self, note_uri: str, actor_uri: str) -> Boost | None:
        """Fetch a boost by note URI and actor URI.

        Used for idempotency checks: returns the existing boost record if
        the local actor has already boosted this note.

        Args:
            note_uri: The ActivityPub URI of the boosted note.
            actor_uri: The ActivityPub URI of the actor who boosted.

        Returns:
            The matching boost, or ``None`` if no boost exists for the
            given note and actor combination.
        """
        result = await self._session.execute(
            select(Boost).where(Boost.note_uri == note_uri, Boost.actor_uri == actor_uri)
        )
        return result.scalars().first()

    async def get_by_activity_uri(self, activity_uri: str) -> Boost | None:
        """Fetch a boost by its ActivityPub activity URI.

        The activity URI uniquely identifies the Announce activity itself,
        and is used for matching incoming ``Undo{Announce}`` activities to
        the original boost.

        Args:
            activity_uri: The ActivityPub URI of the Announce activity.

        Returns:
            The matching boost, or ``None`` if no boost exists with the
            given activity URI.
        """
        result = await self._session.execute(
            select(Boost).where(Boost.activity_uri == activity_uri)
        )
        return result.scalars().first()
