"""Base repository with common CRUD operations.

Provides a generic repository class that all domain-specific repositories
inherit from. Encapsulates common data access patterns (get, list, add,
delete, commit) so that subclasses only need to define domain-specific
query methods.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import UUIDModel


class BaseRepository[T: UUIDModel]:
    """Generic repository providing common CRUD operations.

    All domain-specific repositories inherit from this class, gaining
    consistent get-by-id, list-all, add, delete, and commit behaviour.
    Subclasses specify the model type via the generic parameter and pass
    the concrete model class to the constructor.

    Args:
        session: The async database session to use for queries.
        model_class: The SQLAlchemy model class this repository manages.
    """

    def __init__(self, session: AsyncSession, model_class: type[T]) -> None:
        """Initialise the repository with a session and model class.

        Args:
            session: The async database session to use for queries.
            model_class: The SQLAlchemy model class this repository manages.
        """
        self._session = session
        self._model_class = model_class

    async def get_by_id(self, entity_id: uuid.UUID) -> T | None:
        """Fetch an entity by its UUID primary key.

        Args:
            entity_id: The UUID of the entity to retrieve.

        Returns:
            The entity if found, or ``None`` if no entity exists with
            the given ID.
        """
        return await self._session.get(self._model_class, entity_id)

    async def get_all(self) -> Sequence[T]:
        """Fetch all entities of this type.

        Returns:
            A sequence of all entities managed by this repository.
        """
        result = await self._session.execute(select(self._model_class))
        return result.scalars().all()

    async def add(self, entity: T) -> T:
        """Add a new entity to the session.

        The entity is flushed to the database (but not committed) so that
        server-generated defaults (e.g. ``created_at``) are populated on
        the returned object.

        Args:
            entity: The entity instance to persist.

        Returns:
            The same entity, refreshed with any server-generated values.
        """
        self._session.add(entity)
        await self._session.flush()
        await self._session.refresh(entity)
        return entity

    async def delete(self, entity: T) -> None:
        """Delete an entity from the session.

        The deletion is flushed to the database but not committed.
        Call :meth:`commit` to finalise the transaction.

        Args:
            entity: The entity instance to remove.
        """
        await self._session.delete(entity)
        await self._session.flush()

    async def commit(self) -> None:
        """Commit the current transaction.

        Persists all pending changes (adds, updates, deletes) that have
        been flushed during this session.
        """
        await self._session.commit()
