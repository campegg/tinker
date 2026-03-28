"""Following model for tracking accounts the local user follows.

Each record represents a remote actor that the local user has sent
a Follow activity to or is actively following.
"""

from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import UUIDModel


class Following(UUIDModel):
    """A remote actor that the local user follows.

    Attributes:
        id: UUID primary key (inherited from UUIDModel).
        created_at: Timestamp when the follow was initiated (inherited from UUIDModel).
        actor_uri: The ActivityPub URI of the remote actor being followed.
        inbox_url: The inbox URL of the remote actor for delivery.
        display_name: Cached display name of the remote actor.
        avatar_url: Cached local avatar URL for the remote actor.
        status: Follow status — one of "pending", "accepted", or "rejected".
    """

    __tablename__ = "following"

    actor_uri: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    inbox_url: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending", index=True)

    def __repr__(self) -> str:
        """Return a developer-friendly string representation."""
        return f"<Following actor_uri={self.actor_uri!r} status={self.status!r}>"
