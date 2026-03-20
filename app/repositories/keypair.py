"""Keypair repository for accessing the local actor's cryptographic keys.

Provides data access methods for the Keypair model, including retrieval
of the most recently created (active) keypair used for HTTP Signature
signing and public key publication.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from app.models.keypair import Keypair
from app.repositories.base import BaseRepository


class KeypairRepository(BaseRepository[Keypair]):
    """Repository for Keypair entities.

    Extends :class:`BaseRepository` with keypair-specific queries such as
    retrieval of the currently active (most recently created) keypair.

    Args:
        session: The async database session to use for queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialise the repository with a database session.

        Args:
            session: The async database session to use for queries.
        """
        super().__init__(session, Keypair)

    async def get_active(self) -> Keypair | None:
        """Fetch the most recently created keypair.

        The active keypair is the one most recently created, which is used
        for signing outbound HTTP requests and is published in the actor
        document.

        Returns:
            The most recently created keypair, or ``None`` if no keypairs
            exist.
        """
        result = await self._session.execute(
            select(Keypair).order_by(Keypair.created_at.desc()).limit(1)
        )
        return result.scalars().first()
