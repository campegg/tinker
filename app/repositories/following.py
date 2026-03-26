"""Following repository for tracking accounts the local user follows.

Provides data access methods for the Following model, including lookup
by actor URI and filtered retrieval of accepted follow relationships.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

from app.models.following import Following
from app.repositories.base import BaseRepository


class FollowingRepository(BaseRepository[Following]):
    """Repository for Following entities.

    Extends :class:`BaseRepository` with following-specific queries such
    as lookup by actor URI and filtered retrieval of accepted follow
    relationships with pagination.

    Args:
        session: The async database session to use for queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialise the repository with a database session.

        Args:
            session: The async database session to use for queries.
        """
        super().__init__(session, Following)

    async def get_by_actor_uri(self, actor_uri: str) -> Following | None:
        """Fetch a following record by the remote actor's URI.

        Args:
            actor_uri: The ActivityPub URI of the remote actor being followed.

        Returns:
            The matching following record, or ``None`` if no record exists
            for the given actor URI.
        """
        result = await self._session.execute(
            select(Following).where(Following.actor_uri == actor_uri)
        )
        return result.scalars().first()

    async def get_accepted(self, limit: int = 50, offset: int = 0) -> Sequence[Following]:
        """Fetch accepted following relationships with pagination.

        Args:
            limit: Maximum number of records to return. Defaults to 50.
            offset: Number of records to skip for pagination. Defaults to 0.

        Returns:
            A sequence of following records with status ``"accepted"``,
            ordered by creation date descending.
        """
        result = await self._session.execute(
            select(Following)
            .where(Following.status == "accepted")
            .order_by(Following.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    async def get_accepted_before(self, before: datetime, limit: int) -> Sequence[Following]:
        """Fetch accepted following records created before a given timestamp.

        Used for cursor-based pagination of the Following list.

        Args:
            before: The exclusive upper bound on ``created_at``.
            limit: Maximum number of records to return.

        Returns:
            A sequence of accepted following records with
            ``created_at < before``, ordered newest to oldest.
        """
        result = await self._session.execute(
            select(Following)
            .where(Following.status == "accepted", Following.created_at < before)
            .order_by(Following.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_followed_actor_uris(self) -> set[str]:
        """Return the set of actor URIs for all active (pending or accepted) follows.

        Used to batch-check ``is_following`` when rendering notification lists,
        without making one query per notification.

        Returns:
            A set of actor URI strings for all following records with status
            ``"pending"`` or ``"accepted"``.
        """
        result = await self._session.execute(
            select(Following.actor_uri).where(Following.status.in_(("pending", "accepted")))
        )
        return set(result.scalars().all())

    async def count_accepted(self) -> int:
        """Count the total number of accepted following relationships.

        Returns:
            The number of following records with status ``"accepted"``.
        """
        result = await self._session.execute(
            select(func.count()).select_from(Following).where(Following.status == "accepted")
        )
        count: int | None = result.scalar()
        return count if count is not None else 0
