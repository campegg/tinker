"""Delivery queue repository for outbound ActivityPub delivery management.

Provides data access methods for the DeliveryQueue model, including
retrieval of pending deliveries and items eligible for retry based on
their scheduled retry time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

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
