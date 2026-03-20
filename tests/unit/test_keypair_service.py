"""Tests for the keypair service and federation actor document builder."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.keypair import KeypairService


def _make_keypair(public_key: str = "PUBLIC", private_key: str = "PRIVATE") -> MagicMock:
    kp = MagicMock()
    kp.public_key = public_key
    kp.private_key = private_key
    return kp


@pytest.fixture
def mock_session() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def service(mock_session: AsyncMock) -> KeypairService:
    return KeypairService(mock_session)


class TestGetOrCreate:
    async def test_returns_existing_keypair(self, service: KeypairService) -> None:
        existing = _make_keypair("PUB-EXISTING", "PRIV-EXISTING")
        with patch.object(
            service._repo, "get_active", new_callable=AsyncMock, return_value=existing
        ):
            public_key, private_key = await service.get_or_create()

        assert public_key == "PUB-EXISTING"
        assert private_key == "PRIV-EXISTING"

    async def test_generates_new_keypair_when_none_exists(self, service: KeypairService) -> None:
        with (
            patch.object(service._repo, "get_active", new_callable=AsyncMock, return_value=None),
            patch.object(
                service,
                "generate_keypair",
                new_callable=AsyncMock,
                return_value=("NEW-PUB", "NEW-PRIV"),
            ) as mock_gen,
        ):
            public_key, private_key = await service.get_or_create()

        mock_gen.assert_awaited_once()
        assert public_key == "NEW-PUB"
        assert private_key == "NEW-PRIV"


class TestGetPublicKey:
    async def test_returns_public_key(self, service: KeypairService) -> None:
        existing = _make_keypair("PUB-KEY", "PRIV-KEY")
        with patch.object(
            service._repo, "get_active", new_callable=AsyncMock, return_value=existing
        ):
            result = await service.get_public_key()

        assert result == "PUB-KEY"


class TestGetPrivateKey:
    async def test_returns_private_key(self, service: KeypairService) -> None:
        existing = _make_keypair("PUB-KEY", "PRIV-KEY")
        with patch.object(
            service._repo, "get_active", new_callable=AsyncMock, return_value=existing
        ):
            result = await service.get_private_key()

        assert result == "PRIV-KEY"


class TestGenerateKeypair:
    async def test_generates_valid_rsa_keypair(self, service: KeypairService) -> None:
        mock_add = AsyncMock()
        mock_commit = AsyncMock()
        with (
            patch.object(service._repo, "add", mock_add),
            patch.object(service._repo, "commit", mock_commit),
        ):
            public_key, private_key = await service.generate_keypair()

        assert "BEGIN PUBLIC KEY" in public_key
        assert "END PUBLIC KEY" in public_key
        assert "BEGIN PRIVATE KEY" in private_key
        assert "END PRIVATE KEY" in private_key

    async def test_persists_keypair_to_repository(self, service: KeypairService) -> None:
        mock_add = AsyncMock()
        mock_commit = AsyncMock()
        with (
            patch.object(service._repo, "add", mock_add),
            patch.object(service._repo, "commit", mock_commit),
        ):
            await service.generate_keypair()

        mock_add.assert_awaited_once()
        mock_commit.assert_awaited_once()

        # Verify the entity passed to add has both keys
        assert mock_add.await_args is not None
        entity = mock_add.await_args[0][0]
        assert "BEGIN PUBLIC KEY" in entity.public_key
        assert "BEGIN PRIVATE KEY" in entity.private_key

    async def test_generated_keys_are_unique(self, service: KeypairService) -> None:
        mock_add = AsyncMock()
        mock_commit = AsyncMock()
        with (
            patch.object(service._repo, "add", mock_add),
            patch.object(service._repo, "commit", mock_commit),
        ):
            pub1, priv1 = await service.generate_keypair()
            pub2, priv2 = await service.generate_keypair()

        # Two separate calls should produce different keys
        assert pub1 != pub2
        assert priv1 != priv2


# ---------------------------------------------------------------------------
# Actor document builder
# ---------------------------------------------------------------------------


class TestBuildActorDocument:
    async def test_builds_valid_actor_document(self) -> None:
        from app.federation.actor import build_actor_document

        mock_session = AsyncMock()

        with (
            patch(
                "app.federation.actor.SettingsService.get_display_name",
                new_callable=AsyncMock,
                return_value="Alice",
            ),
            patch(
                "app.federation.actor.SettingsService.get_bio",
                new_callable=AsyncMock,
                return_value="A tester",
            ),
            patch(
                "app.federation.actor.SettingsService.get_avatar",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.federation.actor.KeypairService.get_public_key",
                new_callable=AsyncMock,
                return_value="-----BEGIN PUBLIC KEY-----\nFAKE\n-----END PUBLIC KEY-----",
            ),
        ):
            doc = await build_actor_document(
                domain="example.com",
                username="alice",
                session=mock_session,
            )

        assert doc["@context"] == [
            "https://www.w3.org/ns/activitystreams",
            "https://w3id.org/security/v1",
        ]
        assert doc["type"] == "Person"
        assert doc["id"] == "https://example.com/alice"
        assert doc["preferredUsername"] == "alice"
        assert doc["name"] == "Alice"
        assert doc["summary"] == "A tester"
        assert doc["inbox"] == "https://example.com/alice/inbox"
        assert doc["outbox"] == "https://example.com/alice/outbox"
        assert doc["followers"] == "https://example.com/alice/followers"
        assert doc["following"] == "https://example.com/alice/following"
        assert doc["url"] == "https://example.com/alice"

    async def test_includes_public_key(self) -> None:
        from app.federation.actor import build_actor_document

        mock_session = AsyncMock()
        fake_pub = "-----BEGIN PUBLIC KEY-----\nTEST\n-----END PUBLIC KEY-----"

        with (
            patch(
                "app.federation.actor.SettingsService.get_display_name",
                new_callable=AsyncMock,
                return_value="",
            ),
            patch(
                "app.federation.actor.SettingsService.get_bio",
                new_callable=AsyncMock,
                return_value="",
            ),
            patch(
                "app.federation.actor.SettingsService.get_avatar",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.federation.actor.KeypairService.get_public_key",
                new_callable=AsyncMock,
                return_value=fake_pub,
            ),
        ):
            doc = await build_actor_document(
                domain="example.com",
                username="bob",
                session=mock_session,
            )

        pk = doc["publicKey"]
        assert pk["id"] == "https://example.com/bob#main-key"
        assert pk["owner"] == "https://example.com/bob"
        assert pk["publicKeyPem"] == fake_pub

    async def test_omits_icon_when_no_avatar(self) -> None:
        from app.federation.actor import build_actor_document

        mock_session = AsyncMock()

        with (
            patch(
                "app.federation.actor.SettingsService.get_display_name",
                new_callable=AsyncMock,
                return_value="",
            ),
            patch(
                "app.federation.actor.SettingsService.get_bio",
                new_callable=AsyncMock,
                return_value="",
            ),
            patch(
                "app.federation.actor.SettingsService.get_avatar",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.federation.actor.KeypairService.get_public_key",
                new_callable=AsyncMock,
                return_value="KEY",
            ),
        ):
            doc = await build_actor_document(
                domain="example.com",
                username="alice",
                session=mock_session,
            )

        assert "icon" not in doc

    async def test_includes_icon_when_avatar_set(self) -> None:
        from app.federation.actor import build_actor_document

        mock_session = AsyncMock()

        with (
            patch(
                "app.federation.actor.SettingsService.get_display_name",
                new_callable=AsyncMock,
                return_value="",
            ),
            patch(
                "app.federation.actor.SettingsService.get_bio",
                new_callable=AsyncMock,
                return_value="",
            ),
            patch(
                "app.federation.actor.SettingsService.get_avatar",
                new_callable=AsyncMock,
                return_value="/media/avatar.jpg",
            ),
            patch(
                "app.federation.actor.KeypairService.get_public_key",
                new_callable=AsyncMock,
                return_value="KEY",
            ),
        ):
            doc = await build_actor_document(
                domain="example.com",
                username="alice",
                session=mock_session,
            )

        assert "icon" in doc
        assert doc["icon"]["type"] == "Image"
        assert doc["icon"]["url"] == "/media/avatar.jpg"

    async def test_includes_empty_avatar_string_has_no_icon(self) -> None:
        from app.federation.actor import build_actor_document

        mock_session = AsyncMock()

        with (
            patch(
                "app.federation.actor.SettingsService.get_display_name",
                new_callable=AsyncMock,
                return_value="",
            ),
            patch(
                "app.federation.actor.SettingsService.get_bio",
                new_callable=AsyncMock,
                return_value="",
            ),
            patch(
                "app.federation.actor.SettingsService.get_avatar",
                new_callable=AsyncMock,
                return_value="",
            ),
            patch(
                "app.federation.actor.KeypairService.get_public_key",
                new_callable=AsyncMock,
                return_value="KEY",
            ),
        ):
            doc = await build_actor_document(
                domain="example.com",
                username="alice",
                session=mock_session,
            )

        # Empty string is falsy, so icon should not be present
        assert "icon" not in doc
