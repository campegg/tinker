"""Media attachment repository for accessing uploaded files.

Provides data access methods for the MediaAttachment model, including
retrieval of all attachments associated with a specific note.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

from app.models.media_attachment import MediaAttachment
from app.repositories.base import BaseRepository


class MediaAttachmentRepository(BaseRepository[MediaAttachment]):
    """Repository for MediaAttachment entities.

    Extends :class:`BaseRepository` with media-specific queries such as
    retrieval of all attachments belonging to a given note.

    Args:
        session: The async database session to use for queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialise the repository with a database session.

        Args:
            session: The async database session to use for queries.
        """
        super().__init__(session, MediaAttachment)

    async def get_by_note_id(self, note_id: uuid.UUID) -> Sequence[MediaAttachment]:
        """Fetch all media attachments associated with a note.

        Args:
            note_id: The UUID of the note whose attachments to retrieve.

        Returns:
            A sequence of media attachments belonging to the specified
            note, ordered by upload time ascending.
        """
        result = await self._session.execute(
            select(MediaAttachment)
            .where(MediaAttachment.note_id == note_id)
            .order_by(MediaAttachment.uploaded_at.asc())
        )
        return result.scalars().all()
