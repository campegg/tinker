"""Delivery queue model for outbound ActivityPub activity delivery.

Persists pending deliveries so that incomplete tasks can be re-enqueued
on startup, providing crash recovery for federation fan-out.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003

from sqlalchemy import DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import UUIDModel


class DeliveryQueue(UUIDModel):
    """A queued outbound ActivityPub delivery.

    Each row represents a single delivery attempt of an activity to a
    remote inbox. The queue is persisted to SQLite so incomplete tasks
    survive process restarts.

    Attributes:
        id: UUID primary key.
        created_at: Timestamp when the queue entry was created.
        activity_json: Serialised JSON-LD of the activity to deliver.
        target_inbox: The remote inbox URL to POST the activity to.
        status: Current delivery state — ``"pending"``, ``"delivered"``,
            or ``"failed"``.
        attempts: Number of delivery attempts made so far.
        next_retry_at: Earliest time to retry delivery, or ``None`` if
            not scheduled for retry.
        last_error: Description of the most recent delivery failure,
            or ``None`` if no error has occurred.
    """

    __tablename__ = "ap_delivery_queue"

    activity_json: Mapped[str] = mapped_column(Text, nullable=False)
    target_inbox: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )

    def __repr__(self) -> str:
        """Return a developer-friendly string representation."""
        return (
            f"<DeliveryQueue id={self.id} target_inbox={self.target_inbox!r} "
            f"status={self.status!r} attempts={self.attempts}>"
        )
