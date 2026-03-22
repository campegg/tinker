"""Note repository for accessing locally authored posts.

Provides data access methods for the Note model, including lookup by
ActivityPub ID and paginated retrieval of recent notes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

from app.models.note import Note
from app.repositories.base import BaseRepository


class NoteRepository(BaseRepository[Note]):
    """Repository for Note entities.

    Extends :class:`BaseRepository` with note-specific queries such as
    lookup by ActivityPub ID and paginated retrieval ordered by
    publication date.

    Args:
        session: The async database session to use for queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialise the repository with a database session.

        Args:
            session: The async database session to use for queries.
        """
        super().__init__(session, Note)

    async def get_by_ap_id(self, ap_id: str) -> Note | None:
        """Fetch a note by its ActivityPub object URI.

        Args:
            ap_id: The globally unique ActivityPub URI of the note.

        Returns:
            The matching note, or ``None`` if no note exists with
            the given AP ID.
        """
        result = await self._session.execute(select(Note).where(Note.ap_id == ap_id))
        return result.scalars().first()

    async def get_recent(self, limit: int = 20, offset: int = 0) -> Sequence[Note]:
        """Fetch recent notes ordered by publication date descending.

        Args:
            limit: Maximum number of notes to return. Defaults to 20.
            offset: Number of notes to skip for pagination. Defaults to 0.

        Returns:
            A sequence of notes ordered from newest to oldest.
        """
        result = await self._session.execute(
            select(Note).order_by(Note.published_at.desc()).limit(limit).offset(offset)
        )
        return result.scalars().all()

    async def count(self) -> int:
        """Count the total number of notes.

        Returns:
            The total number of notes in the database.
        """
        result = await self._session.execute(select(func.count()).select_from(Note))
        count: int | None = result.scalar()
        return count if count is not None else 0

    async def get_since_dt(
        self,
        since_dt: datetime,
        limit: int = 20,
    ) -> Sequence[Note]:
        """Fetch notes published strictly after a given timestamp.

        Used by the timeline polling endpoint to return only items newer
        than the most recent item the client has seen.

        Args:
            since_dt: The exclusive lower bound — notes published at or
                before this timestamp are excluded.
            limit: Maximum number of notes to return. Defaults to 20.

        Returns:
            A sequence of notes ordered from newest to oldest.
        """
        result = await self._session.execute(
            select(Note)
            .where(Note.published_at > since_dt)
            .order_by(Note.published_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_before_dt(
        self,
        before_dt: datetime,
        limit: int = 20,
    ) -> Sequence[Note]:
        """Fetch notes published strictly before a given timestamp.

        Used by the timeline pagination endpoint to load older items
        beyond the initial page.

        Args:
            before_dt: The exclusive upper bound — notes published at or
                after this timestamp are excluded.
            limit: Maximum number of notes to return. Defaults to 20.

        Returns:
            A sequence of notes ordered from newest to oldest.
        """
        result = await self._session.execute(
            select(Note)
            .where(Note.published_at < before_dt)
            .order_by(Note.published_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_page(
        self,
        limit: int = 20,
        *,
        before_ap_id: str | None = None,
    ) -> Sequence[Note]:
        """Fetch a page of notes for outbox pagination.

        When ``before_ap_id`` is ``None``, returns the most recent ``limit``
        notes ordered newest-first. When a cursor is supplied, resolves it to
        a ``published_at`` timestamp and returns notes published strictly
        before that point.

        Args:
            limit: Maximum number of notes to return. Defaults to 20.
            before_ap_id: The ``ap_id`` of the exclusive upper cursor. Only
                notes published *before* the note with this AP ID are
                returned. If the AP ID does not resolve to a known note,
                an empty sequence is returned.

        Returns:
            A sequence of notes ordered from newest to oldest.
        """
        if before_ap_id is None:
            return await self.get_recent(limit)

        cursor = await self.get_by_ap_id(before_ap_id)
        if cursor is None:
            return []

        result = await self._session.execute(
            select(Note)
            .where(Note.published_at < cursor.published_at)
            .order_by(Note.published_at.desc())
            .limit(limit)
        )
        return result.scalars().all()
