"""Like model for tracking likes on notes.

Stores both local likes (on remote notes) and remote likes (on local notes).
Each like references the note URI and optionally the actor who performed it.
"""

from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import UUIDModel


class Like(UUIDModel):
    """A like activity associated with a note.

    Attributes:
        id: UUID primary key (inherited from UUIDModel).
        created_at: Timestamp when this record was created (inherited from UUIDModel).
        note_uri: The AP URI of the note that was liked.
        actor_uri: The AP URI of the actor who performed the like.
        activity_uri: The AP URI of the Like activity itself, used for
            deduplication and Undo matching.
    """

    __tablename__ = "likes"

    note_uri: Mapped[str] = mapped_column(Text, nullable=False)
    actor_uri: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    activity_uri: Mapped[str | None] = mapped_column(
        Text, unique=True, nullable=True, default=None
    )

    def __repr__(self) -> str:
        """Return a string representation of the Like."""
        return f"<Like id={self.id} note_uri={self.note_uri!r} actor_uri={self.actor_uri!r}>"
