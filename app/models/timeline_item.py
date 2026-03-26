"""TimelineItem model for storing federated timeline entries.

Each TimelineItem represents an activity received from a remote actor
that should appear in the admin timeline view (e.g. Create, Announce).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003

from sqlalchemy import DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import UUIDModel


class TimelineItem(UUIDModel):
    """A single item in the local timeline.

    Stores activities received from followed actors via the inbox,
    including the rendered content and metadata needed for display.

    Attributes:
        id: UUID primary key (inherited from UUIDModel).
        activity_type: The ActivityPub activity type (e.g. "Create", "Announce").
        actor_uri: The AP URI of the actor who performed the activity.
        actor_name: Cached display name of the actor, if available.
        actor_avatar_url: Local proxy URL for the actor's avatar, if available.
        content: Plain-text or source content of the activity object.
        content_html: Sanitised HTML rendering of the content.
        original_object_uri: The AP URI of the original object (for boosts/replies).
        in_reply_to: The AP URI of the object this item replies to, if any.
        received_at: When this activity was received by the inbox.
        raw_activity: The full activity JSON stored as text for debugging.
        created_at: Row creation timestamp (inherited from UUIDModel).
    """

    __tablename__ = "timeline_items"

    activity_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    actor_uri: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    actor_name: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )

    actor_avatar_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )

    content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )

    content_html: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )

    original_object_uri: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        index=True,
    )

    in_reply_to: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    raw_activity: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )

    def __repr__(self) -> str:
        """Return a developer-friendly string representation."""
        return (
            f"<TimelineItem id={self.id!r} type={self.activity_type!r} actor={self.actor_uri!r}>"
        )
