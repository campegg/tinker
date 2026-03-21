"""Note service for creating, editing, and deleting locally authored posts.

Handles Markdown rendering with typographic processing, ap_id generation,
and the full note lifecycle via the note repository.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from markdown_it import MarkdownIt

from app.models.note import Note
from app.repositories.note import NoteRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Singleton Markdown renderer with typographic substitutions enabled.
# The "commonmark" preset disables the replacements and smartquotes rules
# by default (CommonMark spec excludes smart typography), so they must be
# explicitly re-enabled alongside the typographer option flag.
# This produces: "..." -> ellipsis, "--" -> en dash, "---" -> em dash,
# and straight quotes -> curly quotes.
_md = (
    MarkdownIt("commonmark", {"typographer": True})
    .enable("replacements")  # handles dashes and ellipsis substitutions
    .enable("smartquotes")  # straight quotes -> curly quotes
)


class NoteService:
    """Service for creating, editing, and deleting locally authored notes.

    Wraps :class:`~app.repositories.note.NoteRepository` with Markdown
    rendering, ``ap_id`` generation, and note lifecycle management.

    Args:
        session: The async database session to use.
        domain: The instance domain (e.g. ``"example.com"``).
        username: The local actor's username.
    """

    def __init__(self, session: AsyncSession, domain: str, username: str) -> None:
        """Initialise the note service.

        Args:
            session: The async database session to use.
            domain: The instance domain (e.g. ``"example.com"``).
            username: The local actor's username.
        """
        self._repo = NoteRepository(session)
        self._domain = domain
        self._username = username

    @property
    def actor_uri(self) -> str:
        """Return the canonical ActivityPub actor URI for the local user."""
        return f"https://{self._domain}/{self._username}"

    async def get_by_id(self, note_id: uuid.UUID) -> Note | None:
        """Fetch a note by its UUID primary key.

        Args:
            note_id: The UUID of the note to retrieve.

        Returns:
            The note if found, or ``None``.
        """
        return await self._repo.get_by_id(note_id)

    async def get_by_ap_id(self, ap_id: str) -> Note | None:
        """Fetch a note by its ActivityPub object URI.

        Args:
            ap_id: The AP URI of the note.

        Returns:
            The note if found, or ``None``.
        """
        return await self._repo.get_by_ap_id(ap_id)

    async def create(
        self,
        body: str,
        *,
        in_reply_to: str | None = None,
    ) -> Note:
        """Create and persist a new note.

        Renders ``body`` from Markdown to HTML (with typographic processing),
        assigns a canonical ``ap_id`` derived from the note's UUID, and
        persists the row.

        Args:
            body: The Markdown source of the note.
            in_reply_to: The AP URI of the note being replied to, if any.

        Returns:
            The persisted :class:`~app.models.note.Note`.
        """
        note_id = uuid.uuid4()
        ap_id = f"https://{self._domain}/notes/{note_id}"
        body_html = self.render_markdown(body)
        now = datetime.now(UTC)

        note = Note(
            id=note_id,
            body=body,
            body_html=body_html,
            ap_id=ap_id,
            in_reply_to=in_reply_to,
            published_at=now,
            updated_at=now,
        )
        note = await self._repo.add(note)
        await self._repo.commit()
        return note

    async def edit(self, note: Note, body: str) -> Note:
        """Update the body of an existing note in place.

        Re-renders ``body`` from Markdown to HTML and advances ``updated_at``.
        The previous content is overwritten — no edit history is preserved
        (see §3.2 of the spec).

        Args:
            note: The note to edit.
            body: The new Markdown source.

        Returns:
            The updated :class:`~app.models.note.Note`.
        """
        note.body = body
        note.body_html = self.render_markdown(body)
        note.updated_at = datetime.now(UTC)
        await self._repo.commit()
        return note

    async def delete(self, note: Note) -> None:
        """Delete a note from the database.

        Capture the note's ``ap_id`` before calling this method if it is
        needed to build the corresponding ``Delete`` activity.

        Args:
            note: The note to delete.
        """
        await self._repo.delete(note)
        await self._repo.commit()

    @staticmethod
    def render_markdown(body: str) -> str:
        """Render Markdown source to HTML with typographic processing.

        Applies smart quote substitution, em/en dash conversion, and ellipsis
        normalisation in addition to standard CommonMark rendering.

        Args:
            body: The raw Markdown source string.

        Returns:
            The rendered HTML string.
        """
        result: str = _md.render(body)
        return result
