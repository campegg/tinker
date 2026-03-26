"""Tests for the settings service."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.settings import Settings
from app.services.settings import _DEFAULT_SETTINGS, SettingsService


@pytest.fixture
def mock_session() -> AsyncMock:
    session = AsyncMock()
    return session


@pytest.fixture
def service(mock_session: AsyncMock) -> SettingsService:
    return SettingsService(mock_session)


def _make_setting(key: str, value: str | None) -> Settings:
    setting = MagicMock(spec=Settings)
    setting.key = key
    setting.value = value
    return setting


class TestGet:
    async def test_returns_value_when_key_exists(self, service: SettingsService) -> None:
        setting = _make_setting("display_name", "Alice")
        with patch.object(
            service._repo, "get_by_key", new_callable=AsyncMock, return_value=setting
        ):
            result = await service.get("display_name")
        assert result == "Alice"

    async def test_returns_none_when_key_missing(self, service: SettingsService) -> None:
        with patch.object(service._repo, "get_by_key", new_callable=AsyncMock, return_value=None):
            result = await service.get("nonexistent")
        assert result is None

    async def test_returns_none_when_value_is_none(self, service: SettingsService) -> None:
        setting = _make_setting("avatar", None)
        with patch.object(
            service._repo, "get_by_key", new_callable=AsyncMock, return_value=setting
        ):
            result = await service.get("avatar")
        assert result is None


class TestGetOrDefault:
    async def test_returns_value_when_key_exists(self, service: SettingsService) -> None:
        setting = _make_setting("bio", "Hello world")
        with patch.object(
            service._repo, "get_by_key", new_callable=AsyncMock, return_value=setting
        ):
            result = await service.get_or_default("bio", "fallback")
        assert result == "Hello world"

    async def test_returns_default_when_key_missing(self, service: SettingsService) -> None:
        with patch.object(service._repo, "get_by_key", new_callable=AsyncMock, return_value=None):
            result = await service.get_or_default("bio", "fallback")
        assert result == "fallback"

    async def test_returns_default_when_value_is_none(self, service: SettingsService) -> None:
        setting = _make_setting("avatar", None)
        with patch.object(
            service._repo, "get_by_key", new_callable=AsyncMock, return_value=setting
        ):
            result = await service.get_or_default("avatar", "default.png")
        assert result == "default.png"

    async def test_uses_empty_string_as_default(self, service: SettingsService) -> None:
        with patch.object(service._repo, "get_by_key", new_callable=AsyncMock, return_value=None):
            result = await service.get_or_default("missing")
        assert result == ""


class TestSet:
    async def test_delegates_to_repo_and_commits(self, service: SettingsService) -> None:
        mock_set = AsyncMock()
        mock_commit = AsyncMock()
        with (
            patch.object(service._repo, "set_value", mock_set),
            patch.object(service._repo, "commit", mock_commit),
        ):
            await service.set("display_name", "Bob")

        mock_set.assert_awaited_once_with("display_name", "Bob")
        mock_commit.assert_awaited_once()

    async def test_set_with_none_value(self, service: SettingsService) -> None:
        mock_set = AsyncMock()
        mock_commit = AsyncMock()
        with (
            patch.object(service._repo, "set_value", mock_set),
            patch.object(service._repo, "commit", mock_commit),
        ):
            await service.set("avatar", None)

        mock_set.assert_awaited_once_with("avatar", None)


class TestDisplayName:
    async def test_get_display_name(self, service: SettingsService) -> None:
        setting = _make_setting("display_name", "Alice")
        with patch.object(
            service._repo, "get_by_key", new_callable=AsyncMock, return_value=setting
        ):
            result = await service.get_display_name()
        assert result == "Alice"

    async def test_get_display_name_returns_empty_when_missing(
        self, service: SettingsService
    ) -> None:
        with patch.object(service._repo, "get_by_key", new_callable=AsyncMock, return_value=None):
            result = await service.get_display_name()
        assert result == ""

    async def test_set_display_name(self, service: SettingsService) -> None:
        mock_set = AsyncMock()
        mock_commit = AsyncMock()
        with (
            patch.object(service._repo, "set_value", mock_set),
            patch.object(service._repo, "commit", mock_commit),
        ):
            await service.set_display_name("Charlie")

        mock_set.assert_awaited_once_with("display_name", "Charlie")


class TestBio:
    async def test_get_bio(self, service: SettingsService) -> None:
        setting = _make_setting("bio", "A short bio")
        with patch.object(
            service._repo, "get_by_key", new_callable=AsyncMock, return_value=setting
        ):
            result = await service.get_bio()
        assert result == "A short bio"

    async def test_get_bio_returns_empty_when_missing(self, service: SettingsService) -> None:
        with patch.object(service._repo, "get_by_key", new_callable=AsyncMock, return_value=None):
            result = await service.get_bio()
        assert result == ""

    async def test_set_bio(self, service: SettingsService) -> None:
        mock_set = AsyncMock()
        mock_commit = AsyncMock()
        with (
            patch.object(service._repo, "set_value", mock_set),
            patch.object(service._repo, "commit", mock_commit),
        ):
            await service.set_bio("New bio")

        mock_set.assert_awaited_once_with("bio", "New bio")


class TestAvatar:
    async def test_get_avatar(self, service: SettingsService) -> None:
        setting = _make_setting("avatar", "/media/avatar.jpg")
        with patch.object(
            service._repo, "get_by_key", new_callable=AsyncMock, return_value=setting
        ):
            result = await service.get_avatar()
        assert result == "/media/avatar.jpg"

    async def test_get_avatar_returns_none_when_missing(self, service: SettingsService) -> None:
        with patch.object(service._repo, "get_by_key", new_callable=AsyncMock, return_value=None):
            result = await service.get_avatar()
        assert result is None

    async def test_set_avatar(self, service: SettingsService) -> None:
        mock_set = AsyncMock()
        mock_commit = AsyncMock()
        with (
            patch.object(service._repo, "set_value", mock_set),
            patch.object(service._repo, "commit", mock_commit),
        ):
            await service.set_avatar("/media/new.jpg")

        mock_set.assert_awaited_once_with("avatar", "/media/new.jpg")

    async def test_set_avatar_to_none(self, service: SettingsService) -> None:
        mock_set = AsyncMock()
        mock_commit = AsyncMock()
        with (
            patch.object(service._repo, "set_value", mock_set),
            patch.object(service._repo, "commit", mock_commit),
        ):
            await service.set_avatar(None)

        mock_set.assert_awaited_once_with("avatar", None)


class TestLinks:
    async def test_get_links_parses_json_array(self, service: SettingsService) -> None:
        urls = ["https://example.com", "https://other.com"]
        setting = _make_setting("links", json.dumps(urls))
        with patch.object(
            service._repo, "get_by_key", new_callable=AsyncMock, return_value=setting
        ):
            result = await service.get_links()
        assert result == urls

    async def test_get_links_returns_empty_list_when_missing(
        self, service: SettingsService
    ) -> None:
        with patch.object(service._repo, "get_by_key", new_callable=AsyncMock, return_value=None):
            result = await service.get_links()
        assert result == []

    async def test_get_links_handles_invalid_json(self, service: SettingsService) -> None:
        setting = _make_setting("links", "not valid json")
        with patch.object(
            service._repo, "get_by_key", new_callable=AsyncMock, return_value=setting
        ):
            result = await service.get_links()
        assert result == []

    async def test_get_links_handles_non_array_json(self, service: SettingsService) -> None:
        setting = _make_setting("links", '{"key": "value"}')
        with patch.object(
            service._repo, "get_by_key", new_callable=AsyncMock, return_value=setting
        ):
            result = await service.get_links()
        assert result == []

    async def test_get_links_returns_empty_list_for_empty_array(
        self, service: SettingsService
    ) -> None:
        setting = _make_setting("links", "[]")
        with patch.object(
            service._repo, "get_by_key", new_callable=AsyncMock, return_value=setting
        ):
            result = await service.get_links()
        assert result == []

    async def test_set_links_serializes_to_json(self, service: SettingsService) -> None:
        urls = ["https://example.com", "https://other.com"]
        mock_set = AsyncMock()
        mock_commit = AsyncMock()
        with (
            patch.object(service._repo, "set_value", mock_set),
            patch.object(service._repo, "commit", mock_commit),
        ):
            await service.set_links(urls)

        mock_set.assert_awaited_once_with("links", json.dumps(urls))

    async def test_set_links_empty_list(self, service: SettingsService) -> None:
        mock_set = AsyncMock()
        mock_commit = AsyncMock()
        with (
            patch.object(service._repo, "set_value", mock_set),
            patch.object(service._repo, "commit", mock_commit),
        ):
            await service.set_links([])

        mock_set.assert_awaited_once_with("links", "[]")


class TestGetAllProfile:
    async def test_returns_all_profile_settings(self, service: SettingsService) -> None:
        fake_row = {
            "display_name": "Alice",
            "bio": "Hello",
            "avatar": "/img/avatar.jpg",
            "links": '["https://example.com"]',
        }

        with patch.object(
            service._repo, "get_by_keys", new_callable=AsyncMock, return_value=fake_row
        ):
            result = await service.get_all_profile()

        assert result == {
            "display_name": "Alice",
            "bio": "Hello",
            "avatar": "/img/avatar.jpg",
            "header_image": None,
            "links": ["https://example.com"],
        }

    async def test_returns_defaults_when_settings_missing(self, service: SettingsService) -> None:
        with patch.object(service._repo, "get_by_keys", new_callable=AsyncMock, return_value={}):
            result = await service.get_all_profile()

        assert result == {
            "display_name": "",
            "bio": "",
            "avatar": None,
            "header_image": None,
            "links": [],
        }


class TestSeedDefaults:
    async def test_creates_missing_settings(self, service: SettingsService) -> None:
        mock_set = AsyncMock()
        mock_commit = AsyncMock()
        with (
            patch.object(
                service._repo,
                "get_by_key",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(service._repo, "set_value", mock_set),
            patch.object(service._repo, "commit", mock_commit),
        ):
            await service.seed_defaults()

        assert mock_set.await_count == len(_DEFAULT_SETTINGS)
        mock_commit.assert_awaited_once()

        called_keys = {call.args[0] for call in mock_set.await_args_list}
        assert called_keys == set(_DEFAULT_SETTINGS.keys())

    async def test_does_not_overwrite_existing_settings(self, service: SettingsService) -> None:
        existing = _make_setting("display_name", "Already Set")

        async def fake_get_by_key(key: str) -> Settings | None:
            if key == "display_name":
                return existing
            return None

        mock_set = AsyncMock()
        mock_commit = AsyncMock()
        with (
            patch.object(service._repo, "get_by_key", side_effect=fake_get_by_key),
            patch.object(service._repo, "set_value", mock_set),
            patch.object(service._repo, "commit", mock_commit),
        ):
            await service.seed_defaults()

        # Should not have been called for display_name
        called_keys = {call.args[0] for call in mock_set.await_args_list}
        assert "display_name" not in called_keys
        # But should have been called for the other defaults
        assert mock_set.await_count == len(_DEFAULT_SETTINGS) - 1

    async def test_seed_no_ops_when_all_settings_exist(self, service: SettingsService) -> None:
        async def fake_get_by_key(key: str) -> Settings | None:
            return _make_setting(key, "existing value")

        mock_set = AsyncMock()
        mock_commit = AsyncMock()
        with (
            patch.object(service._repo, "get_by_key", side_effect=fake_get_by_key),
            patch.object(service._repo, "set_value", mock_set),
            patch.object(service._repo, "commit", mock_commit),
        ):
            await service.seed_defaults()

        mock_set.assert_not_awaited()
        # commit is still called even if nothing was seeded
        mock_commit.assert_awaited_once()

    async def test_seed_sets_correct_default_values(self, service: SettingsService) -> None:
        mock_set = AsyncMock()
        mock_commit = AsyncMock()
        with (
            patch.object(
                service._repo,
                "get_by_key",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(service._repo, "set_value", mock_set),
            patch.object(service._repo, "commit", mock_commit),
        ):
            await service.seed_defaults()

        called_values = {call.args[0]: call.args[1] for call in mock_set.await_args_list}
        for key, default in _DEFAULT_SETTINGS.items():
            assert called_values[key] == default, f"Default for '{key}' doesn't match"
