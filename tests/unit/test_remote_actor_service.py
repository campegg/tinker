"""Tests for the remote actor service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.models.remote_actor import RemoteActor
from app.services.remote_actor import RemoteActorService, _MissingFieldError

SAMPLE_ACTOR_DOC: dict[str, object] = {
    "@context": [
        "https://www.w3.org/ns/activitystreams",
        "https://w3id.org/security/v1",
    ],
    "id": "https://mastodon.social/users/alice",
    "type": "Person",
    "preferredUsername": "alice",
    "name": "Alice Smith",
    "summary": "A test user",
    "inbox": "https://mastodon.social/users/alice/inbox",
    "outbox": "https://mastodon.social/users/alice/outbox",
    "followers": "https://mastodon.social/users/alice/followers",
    "following": "https://mastodon.social/users/alice/following",
    "endpoints": {"sharedInbox": "https://mastodon.social/inbox"},
    "icon": {"type": "Image", "url": "https://mastodon.social/avatar.jpg"},
    "image": {"type": "Image", "url": "https://mastodon.social/headers/alice.jpg"},
    "publicKey": {
        "id": "https://mastodon.social/users/alice#main-key",
        "owner": "https://mastodon.social/users/alice",
        "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nFAKE\n-----END PUBLIC KEY-----",
    },
}


@pytest.fixture
def mock_session() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def service(mock_session: AsyncMock) -> RemoteActorService:
    return RemoteActorService(mock_session)


def _make_actor(
    uri: str = "https://remote.example.com/users/alice",
    public_key: str = "-----BEGIN PUBLIC KEY-----\nFAKE\n-----END PUBLIC KEY-----",
    fetched_at: datetime | None = None,
) -> MagicMock:
    actor = MagicMock(spec=RemoteActor)
    actor.uri = uri
    actor.display_name = "Alice"
    actor.handle = "alice@remote.example.com"
    actor.bio = None
    actor.avatar_url = None
    actor.header_image_url = None
    actor.inbox_url = "https://remote.example.com/users/alice/inbox"
    actor.shared_inbox_url = "https://remote.example.com/inbox"
    actor.public_key = public_key
    actor.fetched_at = fetched_at or datetime.now(UTC)
    return actor


def _make_httpx_client(
    *,
    response_json: object = None,
    raise_status_error: bool = False,
    raise_request_error: bool = False,
    raise_json_error: bool = False,
) -> MagicMock:
    """Build a mock httpx.AsyncClient with async context-manager support."""
    mock_response = MagicMock()

    if raise_json_error:
        mock_response.json.side_effect = ValueError("Invalid JSON")
    else:
        mock_response.json.return_value = response_json

    if raise_status_error:
        exc_response = MagicMock()
        exc_response.status_code = 404
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found",
            request=MagicMock(),
            response=exc_response,
        )
    else:
        mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()

    if raise_request_error:
        mock_client.get = AsyncMock(
            side_effect=httpx.RequestError("Connection refused", request=MagicMock())
        )
    else:
        mock_client.get = AsyncMock(return_value=mock_response)

    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


# ------------------------------------------------------------------
# TestGetByUri
# ------------------------------------------------------------------


class TestGetByUri:
    async def test_returns_cached_actor_when_not_expired(
        self, service: RemoteActorService
    ) -> None:
        cached = _make_actor()
        with patch.object(
            service._repo, "get_by_uri", new_callable=AsyncMock, return_value=cached
        ):
            result = await service.get_by_uri("https://remote.example.com/users/alice")

        assert result is cached

    async def test_refetches_when_cached_actor_is_expired(
        self, service: RemoteActorService
    ) -> None:
        expired_time = datetime.now(UTC) - timedelta(seconds=service.CACHE_TTL_SECONDS + 3600)
        stale = _make_actor(fetched_at=expired_time)
        refreshed = _make_actor()

        with (
            patch.object(service._repo, "get_by_uri", new_callable=AsyncMock, return_value=stale),
            patch.object(
                service,
                "fetch_and_cache",
                new_callable=AsyncMock,
                return_value=refreshed,
            ),
        ):
            result = await service.get_by_uri("https://remote.example.com/users/alice")

        assert result is refreshed

    async def test_fetches_and_caches_when_no_cached_actor(
        self, service: RemoteActorService
    ) -> None:
        new_actor = _make_actor()
        with (
            patch.object(service._repo, "get_by_uri", new_callable=AsyncMock, return_value=None),
            patch.object(
                service,
                "fetch_and_cache",
                new_callable=AsyncMock,
                return_value=new_actor,
            ),
        ):
            result = await service.get_by_uri("https://remote.example.com/users/alice")

        assert result is new_actor

    async def test_returns_none_when_fetch_fails(self, service: RemoteActorService) -> None:
        with (
            patch.object(service._repo, "get_by_uri", new_callable=AsyncMock, return_value=None),
            patch.object(service, "fetch_and_cache", new_callable=AsyncMock, return_value=None),
        ):
            result = await service.get_by_uri("https://remote.example.com/users/alice")

        assert result is None


# ------------------------------------------------------------------
# TestFetchActorDocument
# ------------------------------------------------------------------


class TestFetchActorDocument:
    async def test_successfully_fetches_and_parses_json(self, service: RemoteActorService) -> None:
        mock_client = _make_httpx_client(response_json=SAMPLE_ACTOR_DOC)
        with patch("app.services.remote_actor.get_http_client", return_value=mock_client):
            result = await service.fetch_actor_document("https://mastodon.social/users/alice")

        assert result == SAMPLE_ACTOR_DOC
        mock_client.get.assert_awaited_once()

    async def test_returns_none_on_http_error(self, service: RemoteActorService) -> None:
        mock_client = _make_httpx_client(raise_status_error=True)
        with patch("app.services.remote_actor.get_http_client", return_value=mock_client):
            result = await service.fetch_actor_document("https://mastodon.social/users/alice")

        assert result is None

    async def test_returns_none_on_network_error(self, service: RemoteActorService) -> None:
        mock_client = _make_httpx_client(raise_request_error=True)
        with patch("app.services.remote_actor.get_http_client", return_value=mock_client):
            result = await service.fetch_actor_document("https://mastodon.social/users/alice")

        assert result is None

    async def test_returns_none_on_invalid_json(self, service: RemoteActorService) -> None:
        mock_client = _make_httpx_client(raise_json_error=True)
        with patch("app.services.remote_actor.get_http_client", return_value=mock_client):
            result = await service.fetch_actor_document("https://mastodon.social/users/alice")

        assert result is None


# ------------------------------------------------------------------
# TestFetchAndCache
# ------------------------------------------------------------------


class TestFetchAndCache:
    async def test_creates_new_actor_from_valid_document(
        self, service: RemoteActorService
    ) -> None:
        created_actor = _make_actor(uri="https://mastodon.social/users/alice")
        with (
            patch.object(
                service,
                "fetch_actor_document",
                new_callable=AsyncMock,
                return_value=SAMPLE_ACTOR_DOC,
            ),
            patch.object(
                service._repo,
                "get_by_uri",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(
                service._repo,
                "add",
                new_callable=AsyncMock,
                return_value=created_actor,
            ) as mock_add,
            patch.object(service._repo, "commit", new_callable=AsyncMock) as mock_commit,
        ):
            result = await service.fetch_and_cache("https://mastodon.social/users/alice")

        assert result is created_actor
        mock_add.assert_awaited_once()
        mock_commit.assert_awaited_once()

    async def test_updates_existing_actor_when_already_cached(
        self, service: RemoteActorService
    ) -> None:
        existing = _make_actor(uri="https://mastodon.social/users/alice")
        with (
            patch.object(
                service,
                "fetch_actor_document",
                new_callable=AsyncMock,
                return_value=SAMPLE_ACTOR_DOC,
            ),
            patch.object(
                service._repo,
                "get_by_uri",
                new_callable=AsyncMock,
                return_value=existing,
            ),
            patch.object(service._repo, "commit", new_callable=AsyncMock) as mock_commit,
        ):
            result = await service.fetch_and_cache("https://mastodon.social/users/alice")

        assert result is existing
        # Verify fields were updated on the existing actor.
        assert existing.display_name == "Alice Smith"
        assert existing.handle == "alice@mastodon.social"
        assert existing.bio == "A test user"
        assert existing.avatar_url == "https://mastodon.social/avatar.jpg"
        assert existing.header_image_url == "https://mastodon.social/headers/alice.jpg"
        assert existing.inbox_url == "https://mastodon.social/users/alice/inbox"
        assert existing.shared_inbox_url == "https://mastodon.social/inbox"
        assert existing.public_key == (
            "-----BEGIN PUBLIC KEY-----\nFAKE\n-----END PUBLIC KEY-----"
        )
        mock_commit.assert_awaited_once()

    async def test_returns_none_on_fetch_failure(self, service: RemoteActorService) -> None:
        with patch.object(
            service,
            "fetch_actor_document",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await service.fetch_and_cache("https://mastodon.social/users/alice")

        assert result is None

    async def test_returns_none_when_document_missing_required_fields(
        self, service: RemoteActorService
    ) -> None:
        incomplete_doc = {"id": "https://mastodon.social/users/alice"}
        with patch.object(
            service,
            "fetch_actor_document",
            new_callable=AsyncMock,
            return_value=incomplete_doc,
        ):
            result = await service.fetch_and_cache("https://mastodon.social/users/alice")

        assert result is None


# ------------------------------------------------------------------
# TestGetPublicKey
# ------------------------------------------------------------------


class TestGetPublicKey:
    async def test_returns_public_key_when_actor_exists(self, service: RemoteActorService) -> None:
        actor = _make_actor()
        with patch.object(service, "get_by_uri", new_callable=AsyncMock, return_value=actor):
            result = await service.get_public_key("https://remote.example.com/users/alice")

        assert result == actor.public_key

    async def test_returns_none_when_actor_not_found(self, service: RemoteActorService) -> None:
        with patch.object(service, "get_by_uri", new_callable=AsyncMock, return_value=None):
            result = await service.get_public_key("https://remote.example.com/users/alice")

        assert result is None


# ------------------------------------------------------------------
# TestRefresh
# ------------------------------------------------------------------


class TestRefresh:
    async def test_forces_refetch_regardless_of_ttl(self, service: RemoteActorService) -> None:
        refreshed = _make_actor()
        with patch.object(
            service,
            "fetch_and_cache",
            new_callable=AsyncMock,
            return_value=refreshed,
        ) as mock_fetch_and_cache:
            result = await service.refresh("https://remote.example.com/users/alice")

        assert result is refreshed
        mock_fetch_and_cache.assert_awaited_once_with("https://remote.example.com/users/alice")


# ------------------------------------------------------------------
# TestIsExpired
# ------------------------------------------------------------------


class TestIsExpired:
    def test_returns_false_for_freshly_fetched_actor(self, service: RemoteActorService) -> None:
        actor = _make_actor(fetched_at=datetime.now(UTC))
        assert service._is_expired(actor) is False

    def test_returns_true_for_actor_fetched_over_24_hours_ago(
        self, service: RemoteActorService
    ) -> None:
        old_time = datetime.now(UTC) - timedelta(seconds=service.CACHE_TTL_SECONDS + 1)
        actor = _make_actor(fetched_at=old_time)
        assert service._is_expired(actor) is True

    def test_handles_fetched_at_without_timezone_info(self, service: RemoteActorService) -> None:
        # Simulate a naive datetime (no tzinfo) as might come from SQLite.
        naive_past = datetime.now(UTC).replace(tzinfo=None) - timedelta(
            seconds=service.CACHE_TTL_SECONDS + 3600
        )
        actor = _make_actor(fetched_at=naive_past)
        assert service._is_expired(actor) is True

    def test_naive_datetime_within_ttl_is_not_expired(self, service: RemoteActorService) -> None:
        naive_recent = datetime.now(UTC).replace(tzinfo=None)
        actor = _make_actor(fetched_at=naive_recent)
        assert service._is_expired(actor) is False


# ------------------------------------------------------------------
# TestParseActorDocument
# ------------------------------------------------------------------


class TestParseActorDocument:
    def test_parses_complete_mastodon_style_actor_document(self) -> None:
        result = RemoteActorService._parse_actor_document(dict(SAMPLE_ACTOR_DOC))

        assert result["uri"] == "https://mastodon.social/users/alice"
        assert result["display_name"] == "Alice Smith"
        assert result["handle"] == "alice@mastodon.social"
        assert result["bio"] == "A test user"
        assert result["avatar_url"] == "https://mastodon.social/avatar.jpg"
        assert result["header_image_url"] == "https://mastodon.social/headers/alice.jpg"
        assert result["inbox_url"] == "https://mastodon.social/users/alice/inbox"
        assert result["shared_inbox_url"] == "https://mastodon.social/inbox"
        assert result["public_key"] == (
            "-----BEGIN PUBLIC KEY-----\nFAKE\n-----END PUBLIC KEY-----"
        )

    def test_parses_minimal_document_with_only_required_fields(self) -> None:
        minimal: dict[str, object] = {
            "id": "https://other.example.com/users/bob",
            "inbox": "https://other.example.com/users/bob/inbox",
            "publicKey": {
                "id": "https://other.example.com/users/bob#main-key",
                "owner": "https://other.example.com/users/bob",
                "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nMINIMAL\n-----END PUBLIC KEY-----",
            },
        }
        result = RemoteActorService._parse_actor_document(minimal)

        assert result["uri"] == "https://other.example.com/users/bob"
        assert result["inbox_url"] == "https://other.example.com/users/bob/inbox"
        assert result["public_key"] == (
            "-----BEGIN PUBLIC KEY-----\nMINIMAL\n-----END PUBLIC KEY-----"
        )
        assert result["display_name"] is None
        assert result["handle"] is None
        assert result["bio"] is None
        assert result["avatar_url"] is None
        assert result["header_image_url"] is None
        assert result["shared_inbox_url"] is None

    def test_raises_on_missing_id(self) -> None:
        doc: dict[str, object] = {
            "inbox": "https://example.com/inbox",
            "publicKey": {
                "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nK\n-----END PUBLIC KEY-----",
            },
        }
        with pytest.raises(_MissingFieldError, match="id"):
            RemoteActorService._parse_actor_document(doc)

    def test_raises_on_missing_inbox(self) -> None:
        doc: dict[str, object] = {
            "id": "https://example.com/users/alice",
            "publicKey": {
                "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nK\n-----END PUBLIC KEY-----",
            },
        }
        with pytest.raises(_MissingFieldError, match="inbox"):
            RemoteActorService._parse_actor_document(doc)

    def test_raises_on_missing_public_key_pem(self) -> None:
        doc: dict[str, object] = {
            "id": "https://example.com/users/alice",
            "inbox": "https://example.com/users/alice/inbox",
            "publicKey": {
                "id": "https://example.com/users/alice#main-key",
            },
        }
        with pytest.raises(_MissingFieldError, match=r"publicKey\.publicKeyPem"):
            RemoteActorService._parse_actor_document(doc)

    def test_raises_on_missing_public_key_object(self) -> None:
        doc: dict[str, object] = {
            "id": "https://example.com/users/alice",
            "inbox": "https://example.com/users/alice/inbox",
        }
        with pytest.raises(_MissingFieldError, match="publicKey"):
            RemoteActorService._parse_actor_document(doc)

    def test_extracts_handle_from_preferred_username_and_domain(self) -> None:
        doc: dict[str, object] = {
            "id": "https://instance.example.org/users/charlie",
            "preferredUsername": "charlie",
            "inbox": "https://instance.example.org/users/charlie/inbox",
            "publicKey": {
                "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nK\n-----END PUBLIC KEY-----",
            },
        }
        result = RemoteActorService._parse_actor_document(doc)
        assert result["handle"] == "charlie@instance.example.org"

    def test_extracts_avatar_from_icon_url(self) -> None:
        doc: dict[str, object] = {
            "id": "https://example.com/users/alice",
            "inbox": "https://example.com/users/alice/inbox",
            "icon": {"type": "Image", "url": "https://example.com/avatars/alice.png"},
            "publicKey": {
                "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nK\n-----END PUBLIC KEY-----",
            },
        }
        result = RemoteActorService._parse_actor_document(doc)
        assert result["avatar_url"] == "https://example.com/avatars/alice.png"

    def test_extracts_shared_inbox_from_endpoints(self) -> None:
        doc: dict[str, object] = {
            "id": "https://example.com/users/alice",
            "inbox": "https://example.com/users/alice/inbox",
            "endpoints": {"sharedInbox": "https://example.com/shared-inbox"},
            "publicKey": {
                "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nK\n-----END PUBLIC KEY-----",
            },
        }
        result = RemoteActorService._parse_actor_document(doc)
        assert result["shared_inbox_url"] == "https://example.com/shared-inbox"

    def test_raises_on_empty_id_string(self) -> None:
        doc: dict[str, object] = {
            "id": "",
            "inbox": "https://example.com/inbox",
            "publicKey": {
                "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nK\n-----END PUBLIC KEY-----",
            },
        }
        with pytest.raises(_MissingFieldError, match="id"):
            RemoteActorService._parse_actor_document(doc)

    def test_raises_on_empty_inbox_string(self) -> None:
        doc: dict[str, object] = {
            "id": "https://example.com/users/alice",
            "inbox": "",
            "publicKey": {
                "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nK\n-----END PUBLIC KEY-----",
            },
        }
        with pytest.raises(_MissingFieldError, match="inbox"):
            RemoteActorService._parse_actor_document(doc)

    def test_raises_on_empty_public_key_pem_string(self) -> None:
        doc: dict[str, object] = {
            "id": "https://example.com/users/alice",
            "inbox": "https://example.com/users/alice/inbox",
            "publicKey": {
                "publicKeyPem": "",
            },
        }
        with pytest.raises(_MissingFieldError, match=r"publicKey\.publicKeyPem"):
            RemoteActorService._parse_actor_document(doc)

    def test_handle_is_none_when_preferred_username_missing(self) -> None:
        doc: dict[str, object] = {
            "id": "https://example.com/users/alice",
            "inbox": "https://example.com/users/alice/inbox",
            "publicKey": {
                "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nK\n-----END PUBLIC KEY-----",
            },
        }
        result = RemoteActorService._parse_actor_document(doc)
        assert result["handle"] is None

    def test_avatar_is_none_when_icon_missing(self) -> None:
        doc: dict[str, object] = {
            "id": "https://example.com/users/alice",
            "inbox": "https://example.com/users/alice/inbox",
            "publicKey": {
                "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nK\n-----END PUBLIC KEY-----",
            },
        }
        result = RemoteActorService._parse_actor_document(doc)
        assert result["avatar_url"] is None

    def test_shared_inbox_is_none_when_endpoints_missing(self) -> None:
        doc: dict[str, object] = {
            "id": "https://example.com/users/alice",
            "inbox": "https://example.com/users/alice/inbox",
            "publicKey": {
                "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nK\n-----END PUBLIC KEY-----",
            },
        }
        result = RemoteActorService._parse_actor_document(doc)
        assert result["shared_inbox_url"] is None

    def test_extracts_bio_from_summary(self) -> None:
        doc: dict[str, object] = {
            "id": "https://example.com/users/alice",
            "inbox": "https://example.com/users/alice/inbox",
            "summary": "<p>My bio here</p>",
            "publicKey": {
                "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nK\n-----END PUBLIC KEY-----",
            },
        }
        result = RemoteActorService._parse_actor_document(doc)
        assert result["bio"] == "<p>My bio here</p>"

    def test_bio_is_none_when_summary_missing(self) -> None:
        doc: dict[str, object] = {
            "id": "https://example.com/users/alice",
            "inbox": "https://example.com/users/alice/inbox",
            "publicKey": {
                "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nK\n-----END PUBLIC KEY-----",
            },
        }
        result = RemoteActorService._parse_actor_document(doc)
        assert result["bio"] is None

    def test_bio_is_none_when_summary_is_empty_string(self) -> None:
        doc: dict[str, object] = {
            "id": "https://example.com/users/alice",
            "inbox": "https://example.com/users/alice/inbox",
            "summary": "",
            "publicKey": {
                "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nK\n-----END PUBLIC KEY-----",
            },
        }
        result = RemoteActorService._parse_actor_document(doc)
        assert result["bio"] is None

    def test_extracts_header_image_from_image_field(self) -> None:
        doc: dict[str, object] = {
            "id": "https://example.com/users/alice",
            "inbox": "https://example.com/users/alice/inbox",
            "image": {"type": "Image", "url": "https://example.com/headers/alice.jpg"},
            "publicKey": {
                "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nK\n-----END PUBLIC KEY-----",
            },
        }
        result = RemoteActorService._parse_actor_document(doc)
        assert result["header_image_url"] == "https://example.com/headers/alice.jpg"

    def test_header_image_is_none_when_image_missing(self) -> None:
        doc: dict[str, object] = {
            "id": "https://example.com/users/alice",
            "inbox": "https://example.com/users/alice/inbox",
            "publicKey": {
                "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nK\n-----END PUBLIC KEY-----",
            },
        }
        result = RemoteActorService._parse_actor_document(doc)
        assert result["header_image_url"] is None

    def test_header_image_is_none_when_image_has_no_url(self) -> None:
        doc: dict[str, object] = {
            "id": "https://example.com/users/alice",
            "inbox": "https://example.com/users/alice/inbox",
            "image": {"type": "Image"},
            "publicKey": {
                "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nK\n-----END PUBLIC KEY-----",
            },
        }
        result = RemoteActorService._parse_actor_document(doc)
        assert result["header_image_url"] is None

    def test_bio_script_tags_are_stripped_by_sanitisation(self) -> None:
        """Malicious script tags in a remote actor's summary are stripped by nh3."""
        doc: dict[str, object] = {
            "id": "https://example.com/users/evil",
            "inbox": "https://example.com/users/evil/inbox",
            "summary": '<p>Hello</p><script>alert("xss")</script>',
            "publicKey": {
                "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nK\n-----END PUBLIC KEY-----",
            },
        }
        result = RemoteActorService._parse_actor_document(doc)
        bio = result["bio"]
        assert bio is not None
        assert "<script>" not in bio
        assert "alert" not in bio
        assert "<p>Hello</p>" in bio

    def test_bio_dangerous_attributes_are_stripped_by_sanitisation(self) -> None:
        """Event handler attributes in a remote actor's summary are stripped by nh3."""
        doc: dict[str, object] = {
            "id": "https://example.com/users/evil",
            "inbox": "https://example.com/users/evil/inbox",
            "summary": '<p onmouseover="alert(1)">Hover me</p>',
            "publicKey": {
                "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nK\n-----END PUBLIC KEY-----",
            },
        }
        result = RemoteActorService._parse_actor_document(doc)
        bio = result["bio"]
        assert bio is not None
        assert "onmouseover" not in bio
        assert "alert" not in bio
        assert "Hover me" in bio
