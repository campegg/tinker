"""Follower model for tracking actors who follow the local user.

Each record represents a remote actor that has sent a Follow activity
to the local user. The status field tracks the state of the follow
relationship (pending, accepted, or rejected).
"""

from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import UUIDModel


class Follower(UUIDModel):
    """A remote actor who follows the local user.

    Attributes:
        id: Unique identifier (UUID primary key, inherited from UUIDModel).
        created_at: Timestamp when the record was created (inherited from UUIDModel).
        actor_uri: The unique ActivityPub URI of the remote actor.
        inbox_url: The actor's personal inbox URL for direct delivery.
        shared_inbox_url: The actor's shared inbox URL, if available.
        display_name: The actor's display name, if known.
        avatar_url: Local path to the proxied avatar image, if available.
        status: Follow relationship state — "pending", "accepted", or "rejected".
    """

    __tablename__ = "followers"

    actor_uri: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    inbox_url: Mapped[str] = mapped_column(Text, nullable=False)
    shared_inbox_url: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")

    def __repr__(self) -> str:
        """Return a developer-friendly string representation."""
        return f"<Follower actor_uri={self.actor_uri!r} status={self.status!r}>"
