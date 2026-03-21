"""Unit tests for Follow and Undo{Follow} activity builders.

Tests the pure builder functions in :mod:`app.federation.follow`.
These functions perform no I/O and can be tested without a database
or running application.
"""

from __future__ import annotations

import pytest

from app.federation.follow import build_follow_activity, build_undo_follow_activity
from app.federation.outbox import AP_CONTEXT

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACTOR_URI = "https://example.com/alice"
TARGET_URI = "https://remote.example.com/users/bob"
ACTIVITY_ID = "https://example.com/follows/abc-123"


# ---------------------------------------------------------------------------
# build_follow_activity
# ---------------------------------------------------------------------------


class TestBuildFollowActivity:
    """Tests for :func:`build_follow_activity`."""

    def test_type_is_follow(self) -> None:
        """Activity type must be ``"Follow"``."""
        result = build_follow_activity(ACTOR_URI, TARGET_URI, ACTIVITY_ID)
        assert result["type"] == "Follow"

    def test_actor_field(self) -> None:
        """Actor field must match the supplied actor URI."""
        result = build_follow_activity(ACTOR_URI, TARGET_URI, ACTIVITY_ID)
        assert result["actor"] == ACTOR_URI

    def test_object_field(self) -> None:
        """Object field must be the target actor URI."""
        result = build_follow_activity(ACTOR_URI, TARGET_URI, ACTIVITY_ID)
        assert result["object"] == TARGET_URI

    def test_id_field(self) -> None:
        """Activity id must match the supplied activity_id."""
        result = build_follow_activity(ACTOR_URI, TARGET_URI, ACTIVITY_ID)
        assert result["id"] == ACTIVITY_ID

    def test_context_present(self) -> None:
        """Top-level ``@context`` must be the ActivityStreams URI."""
        result = build_follow_activity(ACTOR_URI, TARGET_URI, ACTIVITY_ID)
        assert result["@context"] == AP_CONTEXT

    def test_returns_dict(self) -> None:
        """Return type is a plain Python dict."""
        result = build_follow_activity(ACTOR_URI, TARGET_URI, ACTIVITY_ID)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# build_undo_follow_activity
# ---------------------------------------------------------------------------


class TestBuildUndoFollowActivity:
    """Tests for :func:`build_undo_follow_activity`."""

    def test_type_is_undo(self) -> None:
        """Outer activity type must be ``"Undo"``."""
        result = build_undo_follow_activity(ACTOR_URI, TARGET_URI, ACTIVITY_ID)
        assert result["type"] == "Undo"

    def test_actor_field(self) -> None:
        """Outer actor field must be the local actor URI."""
        result = build_undo_follow_activity(ACTOR_URI, TARGET_URI, ACTIVITY_ID)
        assert result["actor"] == ACTOR_URI

    def test_context_present(self) -> None:
        """Top-level ``@context`` must be present."""
        result = build_undo_follow_activity(ACTOR_URI, TARGET_URI, ACTIVITY_ID)
        assert result["@context"] == AP_CONTEXT

    def test_id_is_undo_uri(self) -> None:
        """Undo activity id must be the Follow id with ``#undo`` suffix."""
        result = build_undo_follow_activity(ACTOR_URI, TARGET_URI, ACTIVITY_ID)
        assert result["id"] == f"{ACTIVITY_ID}#undo"

    def test_embedded_object_is_follow(self) -> None:
        """Embedded ``object`` must be a Follow activity dict."""
        result = build_undo_follow_activity(ACTOR_URI, TARGET_URI, ACTIVITY_ID)
        obj = result["object"]
        assert isinstance(obj, dict)
        assert obj["type"] == "Follow"

    def test_embedded_object_id_matches_follow_id(self) -> None:
        """Embedded Follow ``id`` must match the supplied follow_activity_id."""
        result = build_undo_follow_activity(ACTOR_URI, TARGET_URI, ACTIVITY_ID)
        assert result["object"]["id"] == ACTIVITY_ID

    def test_embedded_object_actor_matches(self) -> None:
        """Embedded Follow ``actor`` must be the local actor URI."""
        result = build_undo_follow_activity(ACTOR_URI, TARGET_URI, ACTIVITY_ID)
        assert result["object"]["actor"] == ACTOR_URI

    def test_embedded_object_target(self) -> None:
        """Embedded Follow ``object`` must be the target actor URI."""
        result = build_undo_follow_activity(ACTOR_URI, TARGET_URI, ACTIVITY_ID)
        assert result["object"]["object"] == TARGET_URI

    def test_returns_dict(self) -> None:
        """Return type is a plain Python dict."""
        result = build_undo_follow_activity(ACTOR_URI, TARGET_URI, ACTIVITY_ID)
        assert isinstance(result, dict)

    @pytest.mark.parametrize(
        "follow_id",
        [
            "https://example.com/follows/uuid-1",
            "https://example.com/follows/uuid-2",
        ],
    )
    def test_undo_id_derived_from_follow_id(self, follow_id: str) -> None:
        """Undo id is always the follow id plus ``#undo``."""
        result = build_undo_follow_activity(ACTOR_URI, TARGET_URI, follow_id)
        assert result["id"] == f"{follow_id}#undo"
        assert result["object"]["id"] == follow_id
