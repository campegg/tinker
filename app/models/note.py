"""Note model for locally authored posts.

Each note represents a single short-form post published by the local actor
into the fediverse. Notes are the primary content type in Tinker.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import UUIDModel

if TYPE_CHECKING:
    from app.models.media_attachment import MediaAttachment


class Note(UUIDModel):
    """A locally authored ActivityPub note.

    Attributes:
        id: UUID primary key (inherited from UUIDModel).
        created_at: When the database row was created (inherited from UUIDModel).
        body: Raw Markdown body of the note.
        body_html: Rendered HTML body of the note.
        ap_id: Globally unique ActivityPub object URI for this note.
        in_reply_to: AP URI of the note this is replying to, if any.
        published_at: When the note was first published.
        updated_at: When the note was last modified.
        attachments: Media files attached to this note.
    """

    __tablename__ = "notes"

    body: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    ap_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    in_reply_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    attachments: Mapped[list[MediaAttachment]] = relationship(
        "MediaAttachment",
        back_populates="note",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        """Return a developer-friendly string representation."""
        truncated = self.body[:40] + "…" if len(self.body) > 40 else self.body
        return f"<Note id={self.id} ap_id={self.ap_id!r} body={truncated!r}>"
