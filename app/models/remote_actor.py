"""RemoteActor model for caching federated ActivityPub actors.

Stores metadata about remote actors discovered through federation,
including their inbox URLs for delivery and public keys for HTTP
Signature verification.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003

from sqlalchemy import DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import UUIDModel


class RemoteActor(UUIDModel):
    """A cached representation of a remote ActivityPub actor.

    Remote actors are discovered when they interact with this instance
    (e.g. sending a Follow, Like, or Announce). Their metadata is cached
    locally to avoid repeated fetches and to support HTTP Signature
    verification.

    Attributes:
        id: UUID primary key (inherited from UUIDModel).
        created_at: When this record was first created (inherited from UUIDModel).
        uri: The canonical ActivityPub URI of the actor (unique).
        display_name: The actor's display name, if available.
        handle: The actor's handle in user@domain format, if known.
        avatar_url: URL to the actor's avatar image (remote URL, not yet proxied).
        inbox_url: The actor's inbox endpoint for direct delivery.
        shared_inbox_url: The actor's shared inbox endpoint, if available.
        public_key: PEM-encoded public key for HTTP Signature verification.
        fetched_at: When this actor's data was last fetched from the remote server.
    """

    __tablename__ = "remote_actors"

    uri: Mapped[str] = mapped_column(
        Text,
        unique=True,
        nullable=False,
    )
    display_name: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )
    handle: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )
    avatar_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )
    inbox_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    shared_inbox_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )
    public_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        """Return a developer-friendly string representation."""
        return f"<RemoteActor uri={self.uri!r} handle={self.handle!r} id={self.id!s}>"
