"""Settings repository for runtime-configurable key-value pairs.

Provides data access methods for the Settings model, including lookup
by setting key and an upsert operation that creates or updates a setting
in a single call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from app.models.settings import Settings
from app.repositories.base import BaseRepository


class SettingsRepository(BaseRepository[Settings]):
    """Repository for Settings entities.

    Extends :class:`BaseRepository` with settings-specific queries such as
    lookup by key name and an upsert operation for creating or updating
    settings values.

    Args:
        session: The async database session to use for queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialise the repository with a database session.

        Args:
            session: The async database session to use for queries.
        """
        super().__init__(session, Settings)

    async def get_by_key(self, key: str) -> Settings | None:
        """Fetch a setting by its unique key name.

        Args:
            key: The setting key to look up (e.g. ``"display_name"``).

        Returns:
            The matching settings row, or ``None`` if no setting exists
            with the given key.
        """
        result = await self._session.execute(select(Settings).where(Settings.key == key))
        return result.scalars().first()

    async def get_by_keys(self, keys: list[str]) -> dict[str, str]:
        """Fetch multiple settings in a single ``WHERE key IN (...)`` query.

        Args:
            keys: The setting keys to look up.

        Returns:
            A dictionary mapping each found key to its value.  Keys that
            do not exist in the database are absent from the result.
        """
        result = await self._session.execute(select(Settings).where(Settings.key.in_(keys)))
        return {row.key: row.value for row in result.scalars().all() if row.value is not None}

    async def set_value(self, key: str, value: str | None) -> Settings:
        """Create or update a setting by key.

        If a setting with the given key already exists, its value is
        updated in place. Otherwise a new settings row is created.
        The change is flushed but not committed — call :meth:`commit`
        to finalise.

        Args:
            key: The setting key to create or update.
            value: The new value for the setting, or ``None`` to clear it.

        Returns:
            The created or updated settings entity.
        """
        existing = await self.get_by_key(key)
        if existing is not None:
            existing.value = value
            await self._session.flush()
            await self._session.refresh(existing)
            return existing

        setting = Settings(key=key, value=value)
        return await self.add(setting)
