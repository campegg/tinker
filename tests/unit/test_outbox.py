"""Unit tests for the ActivityPub outbox activity document builders."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from app.federation.outbox import (
    AP_CONTEXT,
    AP_PUBLIC,
    _extract_tags,
    _to_ap_datetime,
    build_create_activity,
    build_delete_activity,
    build_note_object,
    build_update_activity,
)
from app.models.note import Note

_ACTOR_URI = "https://example.com/alice"
_FOLLOWERS_URL = "https://example.com/alice/followers"
_NOTE_AP_ID = "https://example.com/notes/abc123"


def _make_note(
    body: str = "Hello world",
    body_html: str = "<p>Hello world</p>\n",
    ap_id: str = _NOTE_AP_ID,
    in_reply_to: str | None = None,
    published_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> MagicMock:
    """Build a mock Note with the given field values."""
    note = MagicMock(spec=Note)
    note.body = body
    note.body_html = body_html
    note.ap_id = ap_id
    note.in_reply_to = in_reply_to
    now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    note.published_at = published_at or now
    note.updated_at = updated_at or note.published_at
    note.attachments = []
    return note


# ---------------------------------------------------------------------------
# TestToApDatetime
# ---------------------------------------------------------------------------


class TestToApDatetime:
    def test_formats_utc_datetime_with_z_suffix(self) -> None:
        dt = datetime(2024, 1, 15, 9, 30, 0, tzinfo=UTC)
        result = _to_ap_datetime(dt)
        assert result == "2024-01-15T09:30:00Z"

    def test_handles_naive_datetime_as_utc(self) -> None:
        dt = datetime(2024, 1, 15, 9, 30, 0)  # no tzinfo
        result = _to_ap_datetime(dt)
        assert result == "2024-01-15T09:30:00Z"

    def test_truncates_microseconds(self) -> None:
        dt = datetime(2024, 1, 15, 9, 30, 0, 123456, tzinfo=UTC)
        result = _to_ap_datetime(dt)
        assert result == "2024-01-15T09:30:00Z"


# ---------------------------------------------------------------------------
# TestExtractTags
# ---------------------------------------------------------------------------


class TestExtractTags:
    def test_extracts_single_tag(self) -> None:
        result = _extract_tags("Hello #world")
        assert result == ["world"]

    def test_extracts_multiple_tags(self) -> None:
        result = _extract_tags("#python is great for #activitypub")
        assert "python" in result
        assert "activitypub" in result

    def test_deduplicates_same_tag(self) -> None:
        result = _extract_tags("#rust is great, I love #rust")
        assert result.count("rust") == 1

    def test_lowercases_tags(self) -> None:
        result = _extract_tags("#Python #ACTIVITYPUB")
        assert "python" in result
        assert "activitypub" in result
        assert "Python" not in result

    def test_returns_empty_list_when_no_tags(self) -> None:
        result = _extract_tags("No hashtags here")
        assert result == []

    def test_does_not_match_mid_word_hash(self) -> None:
        # "C#" should not be treated as a hashtag.
        result = _extract_tags("I use C# and F# for work")
        assert result == []

    def test_preserves_insertion_order(self) -> None:
        result = _extract_tags("#alpha and #beta and #gamma")
        assert result == ["alpha", "beta", "gamma"]


# ---------------------------------------------------------------------------
# TestBuildNoteObject
# ---------------------------------------------------------------------------


class TestBuildNoteObject:
    def test_includes_id_and_type(self) -> None:
        note = _make_note()
        result = build_note_object(note, _ACTOR_URI)
        assert result["id"] == _NOTE_AP_ID
        assert result["type"] == "Note"

    def test_attribution_matches_actor_uri(self) -> None:
        note = _make_note()
        result = build_note_object(note, _ACTOR_URI)
        assert result["attributedTo"] == _ACTOR_URI

    def test_to_is_public(self) -> None:
        note = _make_note()
        result = build_note_object(note, _ACTOR_URI)
        assert AP_PUBLIC in result["to"]

    def test_cc_includes_followers(self) -> None:
        note = _make_note()
        result = build_note_object(note, _ACTOR_URI)
        assert _FOLLOWERS_URL in result["cc"]

    def test_content_is_body_html(self) -> None:
        note = _make_note(body_html="<p>Hi</p>\n")
        result = build_note_object(note, _ACTOR_URI)
        assert result["content"] == "<p>Hi</p>\n"

    def test_source_contains_markdown_body(self) -> None:
        note = _make_note(body="Hello **world**")
        result = build_note_object(note, _ACTOR_URI)
        assert result["source"]["content"] == "Hello **world**"
        assert result["source"]["mediaType"] == "text/markdown"

    def test_no_context_key_on_embedded_object(self) -> None:
        note = _make_note()
        result = build_note_object(note, _ACTOR_URI)
        assert "@context" not in result

    def test_omits_updated_when_not_edited(self) -> None:
        ts = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        note = _make_note(published_at=ts, updated_at=ts)
        result = build_note_object(note, _ACTOR_URI)
        assert "updated" not in result

    def test_includes_updated_when_edited(self) -> None:
        published = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        updated = published + timedelta(hours=1)
        note = _make_note(published_at=published, updated_at=updated)
        result = build_note_object(note, _ACTOR_URI)
        assert "updated" in result
        assert result["updated"] == "2024-06-01T13:00:00Z"

    def test_omits_in_reply_to_when_none(self) -> None:
        note = _make_note(in_reply_to=None)
        result = build_note_object(note, _ACTOR_URI)
        assert "inReplyTo" not in result

    def test_includes_in_reply_to_when_set(self) -> None:
        reply_uri = "https://other.example.com/notes/original"
        note = _make_note(in_reply_to=reply_uri)
        result = build_note_object(note, _ACTOR_URI)
        assert result["inReplyTo"] == reply_uri

    def test_extracts_hashtags_into_tag_array(self) -> None:
        note = _make_note(body="Hello #world and #python")
        result = build_note_object(note, _ACTOR_URI)
        tag_names = {t["name"] for t in result["tag"]}
        assert "#world" in tag_names
        assert "#python" in tag_names

    def test_tag_objects_have_required_fields(self) -> None:
        note = _make_note(body="Hello #tinker")
        result = build_note_object(note, _ACTOR_URI)
        tag = result["tag"][0]
        assert tag["type"] == "Hashtag"
        assert tag["name"] == "#tinker"
        assert "href" in tag
        assert "/tags/tinker" in tag["href"]

    def test_empty_tag_array_when_no_hashtags(self) -> None:
        note = _make_note(body="No hashtags here")
        result = build_note_object(note, _ACTOR_URI)
        assert result["tag"] == []

    def test_attachment_is_empty_list(self) -> None:
        note = _make_note()
        result = build_note_object(note, _ACTOR_URI)
        assert result["attachment"] == []

    def test_sensitive_is_false(self) -> None:
        note = _make_note()
        result = build_note_object(note, _ACTOR_URI)
        assert result["sensitive"] is False

    def test_handles_naive_published_at(self) -> None:
        naive_ts = datetime(2024, 6, 1, 12, 0, 0)  # no tzinfo
        note = _make_note(published_at=naive_ts, updated_at=naive_ts)
        result = build_note_object(note, _ACTOR_URI)
        assert result["published"] == "2024-06-01T12:00:00Z"


# ---------------------------------------------------------------------------
# TestBuildCreateActivity
# ---------------------------------------------------------------------------


class TestBuildCreateActivity:
    def test_has_context(self) -> None:
        note = _make_note()
        result = build_create_activity(note, _ACTOR_URI)
        assert result["@context"] == AP_CONTEXT

    def test_type_is_create(self) -> None:
        note = _make_note()
        result = build_create_activity(note, _ACTOR_URI)
        assert result["type"] == "Create"

    def test_id_is_note_ap_id_plus_activity(self) -> None:
        note = _make_note()
        result = build_create_activity(note, _ACTOR_URI)
        assert result["id"] == f"{_NOTE_AP_ID}/activity"

    def test_actor_matches(self) -> None:
        note = _make_note()
        result = build_create_activity(note, _ACTOR_URI)
        assert result["actor"] == _ACTOR_URI

    def test_to_is_public(self) -> None:
        note = _make_note()
        result = build_create_activity(note, _ACTOR_URI)
        assert AP_PUBLIC in result["to"]

    def test_cc_includes_followers(self) -> None:
        note = _make_note()
        result = build_create_activity(note, _ACTOR_URI)
        assert _FOLLOWERS_URL in result["cc"]

    def test_object_is_embedded_note(self) -> None:
        note = _make_note()
        result = build_create_activity(note, _ACTOR_URI)
        assert isinstance(result["object"], dict)
        assert result["object"]["type"] == "Note"
        assert result["object"]["id"] == _NOTE_AP_ID

    def test_object_has_no_nested_context(self) -> None:
        note = _make_note()
        result = build_create_activity(note, _ACTOR_URI)
        assert "@context" not in result["object"]

    def test_published_matches_note_published_at(self) -> None:
        published = datetime(2024, 3, 15, 8, 0, 0, tzinfo=UTC)
        note = _make_note(published_at=published, updated_at=published)
        result = build_create_activity(note, _ACTOR_URI)
        assert result["published"] == "2024-03-15T08:00:00Z"


# ---------------------------------------------------------------------------
# TestBuildUpdateActivity
# ---------------------------------------------------------------------------


class TestBuildUpdateActivity:
    def test_type_is_update(self) -> None:
        note = _make_note()
        result = build_update_activity(note, _ACTOR_URI)
        assert result["type"] == "Update"

    def test_has_context(self) -> None:
        note = _make_note()
        result = build_update_activity(note, _ACTOR_URI)
        assert result["@context"] == AP_CONTEXT

    def test_id_includes_updated_at_timestamp(self) -> None:
        updated = datetime(2024, 6, 1, 14, 0, 0, tzinfo=UTC)
        note = _make_note(updated_at=updated)
        result = build_update_activity(note, _ACTOR_URI)
        assert result["id"] == f"{_NOTE_AP_ID}#updates/2024-06-01T14:00:00Z"

    def test_published_is_updated_at(self) -> None:
        updated = datetime(2024, 6, 1, 14, 0, 0, tzinfo=UTC)
        note = _make_note(updated_at=updated)
        result = build_update_activity(note, _ACTOR_URI)
        assert result["published"] == "2024-06-01T14:00:00Z"

    def test_object_is_embedded_note(self) -> None:
        note = _make_note()
        result = build_update_activity(note, _ACTOR_URI)
        assert result["object"]["type"] == "Note"


# ---------------------------------------------------------------------------
# TestBuildDeleteActivity
# ---------------------------------------------------------------------------


class TestBuildDeleteActivity:
    def test_type_is_delete(self) -> None:
        result = build_delete_activity(_NOTE_AP_ID, _ACTOR_URI)
        assert result["type"] == "Delete"

    def test_has_context(self) -> None:
        result = build_delete_activity(_NOTE_AP_ID, _ACTOR_URI)
        assert result["@context"] == AP_CONTEXT

    def test_id_is_note_ap_id_plus_delete_fragment(self) -> None:
        result = build_delete_activity(_NOTE_AP_ID, _ACTOR_URI)
        assert result["id"] == f"{_NOTE_AP_ID}#delete"

    def test_actor_matches(self) -> None:
        result = build_delete_activity(_NOTE_AP_ID, _ACTOR_URI)
        assert result["actor"] == _ACTOR_URI

    def test_to_is_public(self) -> None:
        result = build_delete_activity(_NOTE_AP_ID, _ACTOR_URI)
        assert AP_PUBLIC in result["to"]

    def test_object_is_tombstone(self) -> None:
        result = build_delete_activity(_NOTE_AP_ID, _ACTOR_URI)
        assert result["object"]["type"] == "Tombstone"
        assert result["object"]["id"] == _NOTE_AP_ID

    def test_published_is_a_valid_datetime_string(self) -> None:
        result = build_delete_activity(_NOTE_AP_ID, _ACTOR_URI)
        # Should be a non-empty ISO 8601 string ending with "Z".
        assert isinstance(result["published"], str)
        assert result["published"].endswith("Z")
