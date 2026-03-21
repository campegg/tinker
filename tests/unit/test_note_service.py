"""Unit tests for the note service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.note import Note
from app.services.note import NoteService


@pytest.fixture
def mock_session() -> AsyncMock:
    """Create a mock async session."""
    return AsyncMock()


@pytest.fixture
def service(mock_session: AsyncMock) -> NoteService:
    """Create a NoteService with a mock session."""
    return NoteService(mock_session, "example.com", "alice")


def _make_note(
    body: str = "Hello world",
    ap_id: str = "https://example.com/notes/test-id",
    published_at: datetime | None = None,
    updated_at: datetime | None = None,
    in_reply_to: str | None = None,
) -> MagicMock:
    """Build a mock Note with the given field values."""
    note = MagicMock(spec=Note)
    note.body = body
    note.body_html = f"<p>{body}</p>\n"
    note.ap_id = ap_id
    note.in_reply_to = in_reply_to
    note.published_at = published_at or datetime.now(UTC)
    note.updated_at = updated_at or note.published_at
    note.attachments = []
    return note


# ---------------------------------------------------------------------------
# TestRenderMarkdown
# ---------------------------------------------------------------------------


class TestRenderMarkdown:
    def test_renders_basic_markdown(self) -> None:
        result = NoteService.render_markdown("Hello **world**")
        assert "<strong>world</strong>" in result

    def test_wraps_plain_text_in_paragraph(self) -> None:
        result = NoteService.render_markdown("Hello world")
        assert result.strip() == "<p>Hello world</p>"

    def test_applies_smart_quotes(self) -> None:
        result = NoteService.render_markdown('"quoted text"')
        # Should contain Unicode curly quotes, not straight ASCII quotes.
        assert "\u201c" in result  # left double quotation mark "
        assert "\u201d" in result  # right double quotation mark "

    def test_applies_em_dash(self) -> None:
        result = NoteService.render_markdown("before---after")
        assert "\u2014" in result  # em dash —

    def test_applies_en_dash(self) -> None:
        result = NoteService.render_markdown("before--after")
        assert "\u2013" in result  # Unicode en dash

    def test_applies_ellipsis(self) -> None:
        result = NoteService.render_markdown("wait...")
        assert "\u2026" in result  # ellipsis …

    def test_empty_string_returns_empty(self) -> None:
        result = NoteService.render_markdown("")
        assert result.strip() == ""

    def test_renders_links(self) -> None:
        result = NoteService.render_markdown("[link](https://example.com)")
        assert 'href="https://example.com"' in result


# ---------------------------------------------------------------------------
# TestActorUri
# ---------------------------------------------------------------------------


class TestActorUri:
    def test_returns_canonical_uri(self, service: NoteService) -> None:
        assert service.actor_uri == "https://example.com/alice"


# ---------------------------------------------------------------------------
# TestNoteServiceCreate
# ---------------------------------------------------------------------------


class TestNoteServiceCreate:
    async def test_ap_id_uses_domain_and_uuid(self, service: NoteService) -> None:
        created = _make_note()
        with (
            patch.object(
                service._repo, "add", new_callable=AsyncMock, return_value=created
            ) as mock_add,
            patch.object(service._repo, "commit", new_callable=AsyncMock),
        ):
            await service.create("Hello")

        added_note: Note = mock_add.call_args[0][0]
        assert added_note.ap_id.startswith("https://example.com/notes/")

    async def test_ap_id_uuid_matches_note_id(self, service: NoteService) -> None:
        created = _make_note()
        with (
            patch.object(
                service._repo, "add", new_callable=AsyncMock, return_value=created
            ) as mock_add,
            patch.object(service._repo, "commit", new_callable=AsyncMock),
        ):
            await service.create("Hello")

        added_note: Note = mock_add.call_args[0][0]
        note_uuid = str(added_note.id)
        assert added_note.ap_id == f"https://example.com/notes/{note_uuid}"

    async def test_body_html_is_rendered_markdown(self, service: NoteService) -> None:
        created = _make_note()
        with (
            patch.object(
                service._repo, "add", new_callable=AsyncMock, return_value=created
            ) as mock_add,
            patch.object(service._repo, "commit", new_callable=AsyncMock),
        ):
            await service.create("Hello **world**")

        added_note: Note = mock_add.call_args[0][0]
        assert "<strong>world</strong>" in added_note.body_html

    async def test_body_preserved_as_raw_markdown(self, service: NoteService) -> None:
        created = _make_note()
        with (
            patch.object(
                service._repo, "add", new_callable=AsyncMock, return_value=created
            ) as mock_add,
            patch.object(service._repo, "commit", new_callable=AsyncMock),
        ):
            await service.create("Hello **world**")

        added_note: Note = mock_add.call_args[0][0]
        assert added_note.body == "Hello **world**"

    async def test_published_at_and_updated_at_are_equal_on_create(
        self, service: NoteService
    ) -> None:
        created = _make_note()
        with (
            patch.object(
                service._repo, "add", new_callable=AsyncMock, return_value=created
            ) as mock_add,
            patch.object(service._repo, "commit", new_callable=AsyncMock),
        ):
            await service.create("Hello")

        added_note: Note = mock_add.call_args[0][0]
        assert added_note.published_at == added_note.updated_at

    async def test_in_reply_to_is_stored(self, service: NoteService) -> None:
        created = _make_note()
        reply_uri = "https://other.example.com/notes/original"
        with (
            patch.object(
                service._repo, "add", new_callable=AsyncMock, return_value=created
            ) as mock_add,
            patch.object(service._repo, "commit", new_callable=AsyncMock),
        ):
            await service.create("My reply", in_reply_to=reply_uri)

        added_note: Note = mock_add.call_args[0][0]
        assert added_note.in_reply_to == reply_uri

    async def test_in_reply_to_defaults_to_none(self, service: NoteService) -> None:
        created = _make_note()
        with (
            patch.object(
                service._repo, "add", new_callable=AsyncMock, return_value=created
            ) as mock_add,
            patch.object(service._repo, "commit", new_callable=AsyncMock),
        ):
            await service.create("Standalone note")

        added_note: Note = mock_add.call_args[0][0]
        assert added_note.in_reply_to is None

    async def test_calls_add_and_commit(self, service: NoteService) -> None:
        created = _make_note()
        with (
            patch.object(
                service._repo, "add", new_callable=AsyncMock, return_value=created
            ) as mock_add,
            patch.object(service._repo, "commit", new_callable=AsyncMock) as mock_commit,
        ):
            result = await service.create("Hello")

        mock_add.assert_awaited_once()
        mock_commit.assert_awaited_once()
        assert result is created

    async def test_each_note_gets_unique_ap_id(self, service: NoteService) -> None:
        note_a = _make_note()
        note_b = _make_note()
        ap_ids: list[str] = []

        async def capture_add(note: Note) -> Note:
            ap_ids.append(note.ap_id)
            return note_a if len(ap_ids) == 1 else note_b

        with (
            patch.object(service._repo, "add", side_effect=capture_add),
            patch.object(service._repo, "commit", new_callable=AsyncMock),
        ):
            await service.create("First")
            await service.create("Second")

        assert len(ap_ids) == 2
        assert ap_ids[0] != ap_ids[1]


# ---------------------------------------------------------------------------
# TestNoteServiceEdit
# ---------------------------------------------------------------------------


class TestNoteServiceEdit:
    async def test_updates_body_and_body_html(self, service: NoteService) -> None:
        note = _make_note(body="Original")
        with patch.object(service._repo, "commit", new_callable=AsyncMock):
            await service.edit(note, "Updated **text**")

        assert note.body == "Updated **text**"
        assert "<strong>text</strong>" in note.body_html

    async def test_advances_updated_at(self, service: NoteService) -> None:
        published = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        note = _make_note(published_at=published, updated_at=published)
        with patch.object(service._repo, "commit", new_callable=AsyncMock):
            await service.edit(note, "New content")

        assert note.updated_at > published

    async def test_does_not_change_published_at(self, service: NoteService) -> None:
        published = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        note = _make_note(published_at=published, updated_at=published)
        with patch.object(service._repo, "commit", new_callable=AsyncMock):
            await service.edit(note, "Changed")

        assert note.published_at == published

    async def test_calls_commit_but_not_add(self, service: NoteService) -> None:
        note = _make_note()
        with (
            patch.object(service._repo, "add", new_callable=AsyncMock) as mock_add,
            patch.object(service._repo, "commit", new_callable=AsyncMock) as mock_commit,
        ):
            result = await service.edit(note, "Changed")

        mock_add.assert_not_awaited()
        mock_commit.assert_awaited_once()
        assert result is note


# ---------------------------------------------------------------------------
# TestNoteServiceDelete
# ---------------------------------------------------------------------------


class TestNoteServiceDelete:
    async def test_calls_delete_and_commit(self, service: NoteService) -> None:
        note = _make_note()
        with (
            patch.object(service._repo, "delete", new_callable=AsyncMock) as mock_delete,
            patch.object(service._repo, "commit", new_callable=AsyncMock) as mock_commit,
        ):
            await service.delete(note)

        mock_delete.assert_awaited_once_with(note)
        mock_commit.assert_awaited_once()

    async def test_delete_does_not_add(self, service: NoteService) -> None:
        note = _make_note()
        with (
            patch.object(service._repo, "add", new_callable=AsyncMock) as mock_add,
            patch.object(service._repo, "delete", new_callable=AsyncMock),
            patch.object(service._repo, "commit", new_callable=AsyncMock),
        ):
            await service.delete(note)

        mock_add.assert_not_awaited()


# ---------------------------------------------------------------------------
# TestNoteServiceGetters
# ---------------------------------------------------------------------------


class TestNoteServiceGetters:
    async def test_get_by_id_delegates_to_repo(self, service: NoteService) -> None:
        note = _make_note()
        note_id = uuid.uuid4()
        with patch.object(
            service._repo, "get_by_id", new_callable=AsyncMock, return_value=note
        ) as mock_get:
            result = await service.get_by_id(note_id)

        mock_get.assert_awaited_once_with(note_id)
        assert result is note

    async def test_get_by_ap_id_delegates_to_repo(self, service: NoteService) -> None:
        note = _make_note()
        ap_id = "https://example.com/notes/abc"
        with patch.object(
            service._repo, "get_by_ap_id", new_callable=AsyncMock, return_value=note
        ) as mock_get:
            result = await service.get_by_ap_id(ap_id)

        mock_get.assert_awaited_once_with(ap_id)
        assert result is note
