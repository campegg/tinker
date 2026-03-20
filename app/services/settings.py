"""Settings service for managing runtime-configurable key-value pairs.

Provides a high-level interface over the settings repository with typed
accessors for known setting keys and a seed method that populates
default values on first run.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.settings import SettingsRepository

# Known setting keys with their default values.
_DEFAULT_SETTINGS: dict[str, str | None] = {
    "display_name": "",
    "bio": "",
    "avatar": None,
    "links": "[]",
}


class SettingsService:
    """Service for reading and writing application settings.

    Wraps :class:`SettingsRepository` to provide typed accessors for
    well-known setting keys and a seeding mechanism for first-run
    initialisation.

    Args:
        session: The async database session used for the lifetime of
            this service instance.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialise the settings service.

        Args:
            session: The async database session to use.
        """
        self._repo = SettingsRepository(session)

    async def get(self, key: str) -> str | None:
        """Retrieve the value of a setting by key.

        Args:
            key: The setting key to look up.

        Returns:
            The setting value, or ``None`` if the key does not exist.
        """
        setting = await self._repo.get_by_key(key)
        if setting is None:
            return None
        return setting.value

    async def get_or_default(self, key: str, default: str = "") -> str:
        """Retrieve a setting value, falling back to a default.

        Args:
            key: The setting key to look up.
            default: The value to return if the key does not exist or
                its value is ``None``.

        Returns:
            The setting value, or *default* if absent or ``None``.
        """
        value = await self.get(key)
        if value is None:
            return default
        return value

    async def set(self, key: str, value: str | None) -> None:
        """Create or update a setting.

        The change is committed immediately.

        Args:
            key: The setting key to set.
            value: The new value, or ``None`` to clear it.
        """
        await self._repo.set_value(key, value)
        await self._repo.commit()

    async def get_display_name(self) -> str:
        """Return the author's display name.

        Returns:
            The display name, or an empty string if not set.
        """
        return await self.get_or_default("display_name", "")

    async def set_display_name(self, name: str) -> None:
        """Update the author's display name.

        Args:
            name: The new display name.
        """
        await self.set("display_name", name)

    async def get_bio(self) -> str:
        """Return the author's biography.

        Returns:
            The biography text (Markdown), or an empty string if not set.
        """
        return await self.get_or_default("bio", "")

    async def set_bio(self, bio: str) -> None:
        """Update the author's biography.

        Args:
            bio: The new biography text (Markdown source).
        """
        await self.set("bio", bio)

    async def get_avatar(self) -> str | None:
        """Return the path to the avatar image.

        Returns:
            The avatar file path, or ``None`` if no avatar is set.
        """
        return await self.get("avatar")

    async def set_avatar(self, path: str | None) -> None:
        """Update the avatar image path.

        Args:
            path: The file path to the avatar image, or ``None`` to
                remove it.
        """
        await self.set("avatar", path)

    async def get_links(self) -> list[str]:
        """Return the list of external profile links.

        Links are stored as a JSON array of URL strings.

        Returns:
            A list of URL strings. Empty list if not set.
        """
        raw = await self.get_or_default("links", "[]")
        try:
            parsed: list[str] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(parsed, list):
            return []
        return parsed

    async def set_links(self, links: list[str]) -> None:
        """Update the list of external profile links.

        Args:
            links: A list of URL strings to store.
        """
        await self.set("links", json.dumps(links))

    async def get_admin_password_hash(self) -> str | None:
        """Return the stored argon2 password hash for the admin user.

        Returns:
            The argon2 hash string, or ``None`` if no password has been set.
        """
        return await self.get("admin_password_hash")

    async def set_admin_password_hash(self, hash_value: str) -> None:
        """Store the argon2 password hash for the admin user.

        Args:
            hash_value: The argon2 hash string to store.
        """
        await self.set("admin_password_hash", hash_value)

    async def get_all_profile(self) -> dict[str, str | list[str] | None]:
        """Return all profile-related settings as a dictionary.

        Convenience method that fetches display name, bio, avatar, and
        links in one call.

        Returns:
            A dictionary with keys ``display_name``, ``bio``, ``avatar``,
            and ``links``.
        """
        return {
            "display_name": await self.get_display_name(),
            "bio": await self.get_bio(),
            "avatar": await self.get_avatar(),
            "links": await self.get_links(),
        }

    async def seed_defaults(self) -> None:
        """Populate default settings if they do not already exist.

        Iterates over the known setting keys and creates any that are
        missing. Existing settings are not overwritten. This should be
        called once during application startup or first-run
        initialisation.
        """
        for key, default_value in _DEFAULT_SETTINGS.items():
            existing = await self._repo.get_by_key(key)
            if existing is None:
                await self._repo.set_value(key, default_value)
        await self._repo.commit()
