"""Remote actor repository for accessing cached federation actor data.

Provides data access methods for the RemoteActor model, including lookup
by canonical ActivityPub URI and by user@domain handle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from app.models.remote_actor import RemoteActor
from app.repositories.base import BaseRepository


class RemoteActorRepository(BaseRepository[RemoteActor]):
    """Repository for RemoteActor entities.

    Extends :class:`BaseRepository` with remote-actor-specific queries
    such as lookup by ActivityPub URI and by user@domain handle.

    Args:
        session: The async database session to use for queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialise the repository with a database session.

        Args:
            session: The async database session to use for queries.
        """
        super().__init__(session, RemoteActor)

    async def get_by_uri(self, uri: str) -> RemoteActor | None:
        """Fetch a remote actor by its canonical ActivityPub URI.

        Args:
            uri: The unique ActivityPub URI of the remote actor.

        Returns:
            The matching remote actor, or ``None`` if no actor exists
            with the given URI.
        """
        result = await self._session.execute(select(RemoteActor).where(RemoteActor.uri == uri))
        return result.scalars().first()

    async def get_by_handle(self, handle: str) -> RemoteActor | None:
        """Fetch a remote actor by its user@domain handle.

        Args:
            handle: The actor's handle in ``user@domain`` format.

        Returns:
            The matching remote actor, or ``None`` if no actor exists
            with the given handle.
        """
        result = await self._session.execute(
            select(RemoteActor).where(RemoteActor.handle == handle)
        )
        return result.scalars().first()
