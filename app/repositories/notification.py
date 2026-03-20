"""Notification repository for accessing notification records.

Provides data access methods for the Notification model, including
paginated retrieval of recent notifications, unread count, and
bulk mark-as-read operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select, update

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.repositories.base import BaseRepository


class NotificationRepository(BaseRepository[Notification]):
    """Repository for Notification entities.

    Extends :class:`BaseRepository` with notification-specific queries
    such as paginated retrieval ordered by creation date, unread count,
    and bulk mark-all-read.

    Args:
        session: The async database session to use for queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialise the repository with a database session.

        Args:
            session: The async database session to use for queries.
        """
        super().__init__(session, Notification)

    async def get_recent(self, limit: int = 20, offset: int = 0) -> Sequence[Notification]:
        """Fetch recent notifications ordered by creation date descending.

        Args:
            limit: Maximum number of notifications to return. Defaults to 20.
            offset: Number of notifications to skip for pagination.
                Defaults to 0.

        Returns:
            A sequence of notifications ordered from newest to oldest.
        """
        result = await self._session.execute(
            select(Notification)
            .order_by(Notification.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    async def get_unread_count(self) -> int:
        """Count the number of unread notifications.

        Returns:
            The total number of notifications where ``read`` is ``False``.
        """
        result = await self._session.execute(
            select(func.count()).select_from(Notification).where(Notification.read == False)  # noqa: E712
        )
        count: int | None = result.scalar()
        return count if count is not None else 0

    async def mark_all_read(self) -> None:
        """Mark all unread notifications as read.

        Updates every notification with ``read=False`` to ``read=True``
        and flushes the change to the database without committing.
        """
        await self._session.execute(
            update(Notification)
            .where(Notification.read == False)  # noqa: E712
            .values(read=True)
        )
        await self._session.flush()
