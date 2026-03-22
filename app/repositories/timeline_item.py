"""Timeline item repository for accessing federated timeline entries.

Provides data access methods for the TimelineItem model, including
cursor-based pagination for timeline rendering and lookup by object URI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

from app.models.timeline_item import TimelineItem
from app.repositories.base import BaseRepository


class TimelineItemRepository(BaseRepository[TimelineItem]):
    """Repository for TimelineItem entities.

    Extends :class:`BaseRepository` with timeline-specific queries such as
    cursor-based pagination ordered by receive time and lookup by the
    original object URI.

    Args:
        session: The async database session to use for queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialise the repository with a database session.

        Args:
            session: The async database session to use for queries.
        """
        super().__init__(session, TimelineItem)

    async def get_recent(
        self,
        limit: int = 20,
        before_id: uuid.UUID | None = None,
    ) -> Sequence[TimelineItem]:
        """Fetch recent timeline items ordered by receive time descending.

        Supports cursor-based pagination via the ``before_id`` parameter.
        When provided, only items received before the item with the given
        ID are returned, enabling infinite-scroll style loading.

        Args:
            limit: Maximum number of items to return. Defaults to 20.
            before_id: If provided, only return items older than the
                timeline item with this UUID. Used as a cursor for
                pagination.

        Returns:
            A sequence of timeline items ordered from newest to oldest.
        """
        stmt = select(TimelineItem)

        if before_id is not None:
            # Subquery to find the received_at of the cursor item
            cursor_subquery = (
                select(TimelineItem.received_at)
                .where(TimelineItem.id == before_id)
                .scalar_subquery()
            )
            stmt = stmt.where(TimelineItem.received_at < cursor_subquery)

        stmt = stmt.order_by(TimelineItem.received_at.desc()).limit(limit)

        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_since_dt(
        self,
        since_dt: datetime,
        limit: int = 20,
    ) -> Sequence[TimelineItem]:
        """Fetch timeline items received strictly after a given timestamp.

        Used by the polling endpoint to return only items newer than the
        most recent item the client has already rendered.

        Args:
            since_dt: The exclusive lower bound — items received at or
                before this timestamp are excluded.
            limit: Maximum number of items to return. Defaults to 20.

        Returns:
            A sequence of timeline items ordered from newest to oldest.
        """
        result = await self._session.execute(
            select(TimelineItem)
            .where(TimelineItem.received_at > since_dt)
            .order_by(TimelineItem.received_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_before_dt(
        self,
        before_dt: datetime,
        limit: int = 20,
    ) -> Sequence[TimelineItem]:
        """Fetch timeline items received strictly before a given timestamp.

        Used by the pagination endpoint to load older items beyond the
        initial page.

        Args:
            before_dt: The exclusive upper bound — items received at or
                after this timestamp are excluded.
            limit: Maximum number of items to return. Defaults to 20.

        Returns:
            A sequence of timeline items ordered from newest to oldest.
        """
        result = await self._session.execute(
            select(TimelineItem)
            .where(TimelineItem.received_at < before_dt)
            .order_by(TimelineItem.received_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_by_object_uri(self, uri: str) -> TimelineItem | None:
        """Fetch a timeline item by its original object URI.

        Looks up the item by the ``original_object_uri`` field, which
        stores the ActivityPub URI of the object the activity refers to.

        Args:
            uri: The ActivityPub URI of the original object.

        Returns:
            The matching timeline item, or ``None`` if no item exists
            with the given object URI.
        """
        result = await self._session.execute(
            select(TimelineItem).where(TimelineItem.original_object_uri == uri)
        )
        return result.scalars().first()

    async def get_by_actor_type_and_object_uri(
        self,
        actor_uri: str,
        activity_type: str,
        original_object_uri: str,
    ) -> TimelineItem | None:
        """Fetch a timeline item matching actor, activity type, and object URI.

        Used for ``Undo{Announce}`` processing: given a received Undo
        wrapping an Announce, identifies the previously stored timeline
        entry so it can be removed.

        Args:
            actor_uri: The AP URI of the actor who performed the activity.
            activity_type: The activity type to match (e.g. ``"Announce"``).
            original_object_uri: The AP URI of the boosted/referenced object.

        Returns:
            The matching timeline item, or ``None`` if not found.
        """
        result = await self._session.execute(
            select(TimelineItem).where(
                TimelineItem.actor_uri == actor_uri,
                TimelineItem.activity_type == activity_type,
                TimelineItem.original_object_uri == original_object_uri,
            )
        )
        return result.scalars().first()
