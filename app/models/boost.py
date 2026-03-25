"""Boost model for tracking outgoing Announce activities.

Stores outgoing boosts (Announce activities) so that Undo{Announce}
can reference the original activity URI when the user un-boosts.
"""

from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import UUIDModel


class Boost(UUIDModel):
    """An outgoing Announce (boost) activity.

    Attributes:
        id: UUID primary key (inherited from UUIDModel).
        created_at: Timestamp when this record was created (inherited from UUIDModel).
        note_uri: The AP URI of the boosted note.
        actor_uri: The AP URI of the local actor who boosted.
        activity_uri: The AP URI of the Announce activity itself, used for
            deduplication and Undo matching.
    """

    __tablename__ = "boosts"

    note_uri: Mapped[str] = mapped_column(Text, nullable=False)
    actor_uri: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    activity_uri: Mapped[str | None] = mapped_column(
        Text, unique=True, nullable=True, default=None
    )

    def __repr__(self) -> str:
        """Return a string representation of the Boost."""
        return f"<Boost id={self.id} note_uri={self.note_uri!r} actor_uri={self.actor_uri!r}>"
