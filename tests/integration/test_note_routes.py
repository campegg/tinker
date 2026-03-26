"""Integration tests for the note object endpoint and outbox collection."""

from __future__ import annotations

from typing import Any

from app.core.config import OUTBOX_PAGE_SIZE
from app.services.note import NoteService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_note(
    app: Any,
    body: str = "Hello **world**",
    in_reply_to: str | None = None,
) -> Any:
    """Create a note in the database via NoteService and return it."""
    async with app.app_context():
        session = app.config["DB_SESSION_FACTORY"]()
        try:
            service = NoteService(session, "test.example.com", "testuser")
            note = await service.create(body, in_reply_to=in_reply_to)
            await session.commit()
            return note
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# TestNoteEndpoint
# ---------------------------------------------------------------------------


class TestNoteEndpoint:
    async def test_ap_client_receives_json_ld(self, client: Any, app: Any) -> None:
        note = await _create_note(app)
        note_id = str(note.id)

        response = await client.get(
            f"/notes/{note_id}",
            headers={"Accept": "application/activity+json"},
        )

        assert response.status_code == 200
        assert "application/activity+json" in response.content_type

    async def test_browser_receives_302_redirect(self, client: Any, app: Any) -> None:
        note = await _create_note(app)
        note_id = str(note.id)

        response = await client.get(
            f"/notes/{note_id}",
            headers={"Accept": "text/html"},
        )

        assert response.status_code == 302

    async def test_redirect_points_to_home(self, client: Any, app: Any) -> None:
        note = await _create_note(app)
        note_id = str(note.id)

        response = await client.get(
            f"/notes/{note_id}",
            headers={"Accept": "text/html"},
        )

        assert response.location == "/"

    async def test_no_accept_header_redirects_browser(self, client: Any, app: Any) -> None:
        note = await _create_note(app)
        note_id = str(note.id)

        response = await client.get(f"/notes/{note_id}")
        assert response.status_code == 302

    async def test_missing_note_returns_404(self, client: Any) -> None:
        import uuid

        fake_id = uuid.uuid4()
        response = await client.get(
            f"/notes/{fake_id}",
            headers={"Accept": "application/activity+json"},
        )
        assert response.status_code == 404

    async def test_invalid_id_returns_404(self, client: Any) -> None:
        response = await client.get(
            "/notes/not-a-uuid",
            headers={"Accept": "application/activity+json"},
        )
        assert response.status_code == 404

    async def test_note_document_has_required_fields(self, client: Any, app: Any) -> None:
        note = await _create_note(app, body="Hello **world**")
        note_id = str(note.id)

        response = await client.get(
            f"/notes/{note_id}",
            headers={"Accept": "application/activity+json"},
        )

        data = await response.get_json()
        assert data["@context"] == [
            "https://www.w3.org/ns/activitystreams",
            "https://w3id.org/security/v1",
        ]
        assert data["type"] == "Note"
        assert data["id"] == f"https://test.example.com/notes/{note_id}"
        assert data["attributedTo"] == "https://test.example.com/testuser"
        assert "content" in data
        assert "published" in data

    async def test_note_document_content_is_rendered_html(self, client: Any, app: Any) -> None:
        note = await _create_note(app, body="Hello **world**")
        note_id = str(note.id)

        response = await client.get(
            f"/notes/{note_id}",
            headers={"Accept": "application/activity+json"},
        )

        data = await response.get_json()
        assert "<strong>world</strong>" in data["content"]

    async def test_note_document_includes_source(self, client: Any, app: Any) -> None:
        note = await _create_note(app, body="Hello **world**")
        note_id = str(note.id)

        response = await client.get(
            f"/notes/{note_id}",
            headers={"Accept": "application/activity+json"},
        )

        data = await response.get_json()
        assert data["source"]["content"] == "Hello **world**"
        assert data["source"]["mediaType"] == "text/markdown"

    async def test_reply_note_includes_in_reply_to(self, client: Any, app: Any) -> None:
        reply_uri = "https://other.example.com/notes/original"
        note = await _create_note(app, body="My reply", in_reply_to=reply_uri)
        note_id = str(note.id)

        response = await client.get(
            f"/notes/{note_id}",
            headers={"Accept": "application/activity+json"},
        )

        data = await response.get_json()
        assert data["inReplyTo"] == reply_uri


# ---------------------------------------------------------------------------
# TestOutboxCollection
# ---------------------------------------------------------------------------


class TestOutboxCollection:
    async def test_root_returns_ordered_collection(self, client: Any) -> None:
        response = await client.get(
            "/testuser/outbox",
            headers={"Accept": "application/activity+json"},
        )
        assert response.status_code == 200
        assert "application/activity+json" in response.content_type

        data = await response.get_json()
        assert data["type"] == "OrderedCollection"

    async def test_root_has_context(self, client: Any) -> None:
        response = await client.get("/testuser/outbox")
        data = await response.get_json()
        assert data["@context"] == [
            "https://www.w3.org/ns/activitystreams",
            "https://w3id.org/security/v1",
        ]

    async def test_root_has_total_items(self, client: Any) -> None:
        response = await client.get("/testuser/outbox")
        data = await response.get_json()
        assert "totalItems" in data
        assert isinstance(data["totalItems"], int)

    async def test_root_reports_zero_posts_initially(self, client: Any) -> None:
        response = await client.get("/testuser/outbox")
        data = await response.get_json()
        assert data["totalItems"] == 0

    async def test_root_total_items_reflects_notes(self, client: Any, app: Any) -> None:
        await _create_note(app, body="First note")
        await _create_note(app, body="Second note")

        response = await client.get("/testuser/outbox")
        data = await response.get_json()
        assert data["totalItems"] == 2

    async def test_root_has_first_link(self, client: Any) -> None:
        response = await client.get("/testuser/outbox")
        data = await response.get_json()
        assert "first" in data
        assert data["first"].endswith("?page=true")

    async def test_root_has_canonical_id(self, client: Any) -> None:
        response = await client.get("/testuser/outbox")
        data = await response.get_json()
        assert data["id"] == "https://test.example.com/testuser/outbox"

    async def test_wrong_username_returns_404(self, client: Any) -> None:
        response = await client.get("/nobody/outbox")
        assert response.status_code == 404

    async def test_page_returns_ordered_collection_page(self, client: Any) -> None:
        response = await client.get("/testuser/outbox?page=true")
        data = await response.get_json()
        assert data["type"] == "OrderedCollectionPage"

    async def test_page_has_context(self, client: Any) -> None:
        response = await client.get("/testuser/outbox?page=true")
        data = await response.get_json()
        assert data["@context"] == [
            "https://www.w3.org/ns/activitystreams",
            "https://w3id.org/security/v1",
        ]

    async def test_page_has_part_of_link(self, client: Any) -> None:
        response = await client.get("/testuser/outbox?page=true")
        data = await response.get_json()
        assert data["partOf"] == "https://test.example.com/testuser/outbox"

    async def test_empty_outbox_page_has_no_items(self, client: Any) -> None:
        response = await client.get("/testuser/outbox?page=true")
        data = await response.get_json()
        assert data["orderedItems"] == []

    async def test_empty_outbox_page_has_no_next_link(self, client: Any) -> None:
        response = await client.get("/testuser/outbox?page=true")
        data = await response.get_json()
        assert "next" not in data

    async def test_page_includes_create_activities(self, client: Any, app: Any) -> None:
        await _create_note(app, body="A note")

        response = await client.get("/testuser/outbox?page=true")
        data = await response.get_json()

        assert len(data["orderedItems"]) == 1
        item = data["orderedItems"][0]
        assert item["type"] == "Create"

    async def test_activity_items_have_no_nested_context(self, client: Any, app: Any) -> None:
        await _create_note(app, body="A note")

        response = await client.get("/testuser/outbox?page=true")
        data = await response.get_json()
        item = data["orderedItems"][0]
        assert "@context" not in item

    async def test_create_activity_has_embedded_note(self, client: Any, app: Any) -> None:
        await _create_note(app, body="Hello **world**")

        response = await client.get("/testuser/outbox?page=true")
        data = await response.get_json()
        item = data["orderedItems"][0]

        assert isinstance(item["object"], dict)
        assert item["object"]["type"] == "Note"
        assert "<strong>world</strong>" in item["object"]["content"]

    async def test_items_ordered_newest_first(self, client: Any, app: Any) -> None:
        await _create_note(app, body="First note")
        await _create_note(app, body="Second note")

        response = await client.get("/testuser/outbox?page=true")
        data = await response.get_json()

        items = data["orderedItems"]
        assert len(items) == 2
        # The most recently published note should be first.
        assert items[0]["object"]["source"]["content"] == "Second note"
        assert items[1]["object"]["source"]["content"] == "First note"

    async def test_next_link_absent_when_fewer_than_page_size(self, client: Any, app: Any) -> None:
        # Create fewer notes than the page size.
        for i in range(OUTBOX_PAGE_SIZE - 5):
            await _create_note(app, body=f"Note {i}")

        response = await client.get("/testuser/outbox?page=true")
        data = await response.get_json()
        assert "next" not in data

    async def test_next_link_present_at_page_boundary(self, client: Any, app: Any) -> None:
        # Create exactly OUTBOX_PAGE_SIZE notes to trigger a "next" link.
        for i in range(OUTBOX_PAGE_SIZE):
            await _create_note(app, body=f"Note {i}")

        response = await client.get("/testuser/outbox?page=true")
        data = await response.get_json()
        assert "next" in data
        assert "max_id=" in data["next"]
        assert "page=true" in data["next"]

    async def test_max_id_pagination_returns_older_items(self, client: Any, app: Any) -> None:
        # Create OUTBOX_PAGE_SIZE + 5 notes so that the second page has items.
        for i in range(OUTBOX_PAGE_SIZE + 5):
            await _create_note(app, body=f"Note {i:02d}")

        # Fetch the first page and extract the "next" cursor.
        first_response = await client.get("/testuser/outbox?page=true")
        first_data = await first_response.get_json()
        assert "next" in first_data

        # Follow the "next" link — strip the base URL to get just the path+query.
        next_url = first_data["next"]
        path = "/" + "/".join(next_url.split("/")[3:])

        second_response = await client.get(path)
        second_data = await second_response.get_json()

        assert second_data["type"] == "OrderedCollectionPage"
        # The second page should have the 5 remaining (oldest) notes.
        assert len(second_data["orderedItems"]) == 5

    async def test_invalid_max_id_returns_empty_page(self, client: Any, app: Any) -> None:
        await _create_note(app, body="A note")

        response = await client.get(
            "/testuser/outbox?max_id=https://test.example.com/notes/nonexistent&page=true"
        )
        data = await response.get_json()
        assert data["orderedItems"] == []
