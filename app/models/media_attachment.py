"""Media attachment model for uploaded files associated with notes.

Each media attachment represents a file (image, etc.) that has been uploaded
and optionally associated with a note. Metadata stripping and optimisation
happen at upload time before the record is created.
"""

from __future__ import annotations

import uuid  # noqa: TC003
from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import UUIDModel

if TYPE_CHECKING:
    from app.models.note import Note


class MediaAttachment(UUIDModel):
    """A media file uploaded and optionally attached to a note.

    Attributes:
        id: UUID primary key (inherited from UUIDModel).
        created_at: Timestamp when the record was created (inherited from UUIDModel).
        note_id: Foreign key to the associated note, or None if unattached.
        file_path: Path to the optimised file on disk relative to the media directory.
        mime_type: MIME type of the stored file (e.g. "image/jpeg").
        alt_text: Optional alt text description for accessibility.
        uploaded_at: Timestamp when the file was uploaded.
        note: The parent note this attachment belongs to, if any.
    """

    __tablename__ = "media_attachments"

    note_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("notes.id"),
        nullable=True,
    )
    file_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    mime_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    alt_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    note: Mapped[Note | None] = relationship(
        "Note",
        back_populates="attachments",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        """Return a developer-friendly string representation."""
        return (
            f"<MediaAttachment id={self.id} mime_type={self.mime_type!r} note_id={self.note_id}>"
        )
