"""Follower repository for accessing actors who follow the local user.

Provides data access methods for the Follower model, including lookup by
actor URI and filtered retrieval of accepted follow relationships.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

from app.models.follower import Follower
from app.repositories.base import BaseRepository


class FollowerRepository(BaseRepository[Follower]):
    """Repository for Follower entities.

    Extends :class:`BaseRepository` with follower-specific queries such as
    lookup by actor URI and paginated retrieval of accepted followers.

    Args:
        session: The async database session to use for queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialise the repository with a database session.

        Args:
            session: The async database session to use for queries.
        """
        super().__init__(session, Follower)

    async def get_by_actor_uri(self, actor_uri: str) -> Follower | None:
        """Fetch a follower by their ActivityPub actor URI.

        Args:
            actor_uri: The canonical ActivityPub URI of the remote actor.

        Returns:
            The matching follower record, or ``None`` if no follower
            exists with the given actor URI.
        """
        result = await self._session.execute(
            select(Follower).where(Follower.actor_uri == actor_uri)
        )
        return result.scalars().first()

    async def get_accepted(self, limit: int = 50, offset: int = 0) -> Sequence[Follower]:
        """Fetch accepted followers ordered by creation date descending.

        Only returns followers whose status is ``"accepted"``.

        Args:
            limit: Maximum number of followers to return. Defaults to 50.
            offset: Number of followers to skip for pagination. Defaults to 0.

        Returns:
            A sequence of accepted followers ordered from newest to oldest.
        """
        result = await self._session.execute(
            select(Follower)
            .where(Follower.status == "accepted")
            .order_by(Follower.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    async def get_all_accepted(self) -> Sequence[Follower]:
        """Fetch all accepted followers without pagination.

        Used by the delivery fan-out pipeline which must deliver to every
        accepted follower, not just the first page.

        Returns:
            A sequence of all followers with status ``"accepted"``,
            ordered by creation date descending.
        """
        result = await self._session.execute(
            select(Follower)
            .where(Follower.status == "accepted")
            .order_by(Follower.created_at.desc())
        )
        return result.scalars().all()

    async def get_accepted_before(self, before: datetime, limit: int) -> Sequence[Follower]:
        """Fetch accepted follower records created before a given timestamp.

        Used for cursor-based pagination of the Followers list.

        Args:
            before: The exclusive upper bound on ``created_at``.
            limit: Maximum number of records to return.

        Returns:
            A sequence of accepted follower records with
            ``created_at < before``, ordered newest to oldest.
        """
        result = await self._session.execute(
            select(Follower)
            .where(Follower.status == "accepted", Follower.created_at < before)
            .order_by(Follower.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def count_accepted(self) -> int:
        """Count the total number of accepted followers.

        Returns:
            The number of followers with status ``"accepted"``.
        """
        result = await self._session.execute(
            select(func.count()).select_from(Follower).where(Follower.status == "accepted")
        )
        count: int | None = result.scalar()
        return count if count is not None else 0

    async def get_by_inbox_url(self, inbox_url: str) -> Follower | None:
        """Fetch a follower whose inbox or shared inbox matches the given URL.

        Used for dead instance detection: after all deliveries to an inbox
        permanently fail, look up the corresponding follower so its status
        can be updated to ``"unreachable"``.

        Checks ``shared_inbox_url`` first (exact match), then ``inbox_url``.

        Args:
            inbox_url: The inbox URL to look up.

        Returns:
            The first matching follower record, or ``None`` if not found.
        """
        from sqlalchemy import or_

        result = await self._session.execute(
            select(Follower).where(
                or_(
                    Follower.shared_inbox_url == inbox_url,
                    Follower.inbox_url == inbox_url,
                )
            )
        )
        return result.scalars().first()
