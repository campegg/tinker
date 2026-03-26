"""Delivery queue repository for outbound ActivityPub delivery management.

Provides data access methods for the DeliveryQueue model, including
retrieval of pending deliveries and items eligible for retry based on
their scheduled retry time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

from app.models.delivery_queue import DeliveryQueue
from app.repositories.base import BaseRepository


class DeliveryQueueRepository(BaseRepository[DeliveryQueue]):
    """Repository for DeliveryQueue entities.

    Extends :class:`BaseRepository` with delivery-specific queries such as
    retrieval of pending deliveries and items whose retry time has arrived.

    Args:
        session: The async database session to use for queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialise the repository with a database session.

        Args:
            session: The async database session to use for queries.
        """
        super().__init__(session, DeliveryQueue)

    async def get_pending(self) -> Sequence[DeliveryQueue]:
        """Fetch all delivery queue items with ``"pending"`` status.

        Returns items that have not yet been attempted or are awaiting
        their first delivery attempt.

        Returns:
            A sequence of pending delivery queue entries.
        """
        result = await self._session.execute(
            select(DeliveryQueue).where(DeliveryQueue.status == "pending")
        )
        return result.scalars().all()

    async def get_never_attempted(self) -> Sequence[DeliveryQueue]:
        """Return pending items that have never been attempted.

        Returns delivery queue entries with ``"pending"`` status whose
        ``next_retry_at`` is ``NULL`` — meaning they were enqueued but
        never dispatched (e.g. the process crashed before the first
        attempt).  This is used by :func:`startup_recovery` to distinguish
        brand-new items from items deliberately held back by exponential
        backoff.

        Returns:
            A sequence of never-attempted pending delivery queue entries.
        """
        result = await self._session.execute(
            select(DeliveryQueue).where(
                DeliveryQueue.status == "pending",
                DeliveryQueue.next_retry_at.is_(None),
            )
        )
        return result.scalars().all()

    async def get_retryable(self) -> Sequence[DeliveryQueue]:
        """Fetch pending items whose retry time has arrived.

        Returns delivery queue entries with ``"pending"`` status whose
        ``next_retry_at`` timestamp is at or before the current UTC time.
        This is used by the background delivery worker to find items that
        should be retried now.

        Returns:
            A sequence of delivery queue entries eligible for retry.
        """
        now = datetime.now(UTC)
        result = await self._session.execute(
            select(DeliveryQueue).where(
                DeliveryQueue.status == "pending",
                DeliveryQueue.next_retry_at <= now,
            )
        )
        return result.scalars().all()

    async def has_recent_success(self, inbox_url: str, *, since: datetime) -> bool:
        """Check whether a successful delivery to an inbox exists since a cutoff.

        Used for dead instance detection: if no successful delivery to a
        given inbox has occurred in the last ``N`` days, the remote server
        may be permanently unreachable.

        Args:
            inbox_url: The inbox URL to check.
            since: Only consider deliveries created after this timestamp.

        Returns:
            ``True`` if at least one ``"delivered"`` entry for the inbox
            exists with ``created_at >= since``, ``False`` otherwise.
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(DeliveryQueue)
            .where(
                DeliveryQueue.target_inbox == inbox_url,
                DeliveryQueue.status == "delivered",
                DeliveryQueue.created_at >= since,
            )
        )
        count: int | None = result.scalar()
        return (count or 0) > 0
