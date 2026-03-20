"""Keypair service for managing the local actor's cryptographic keys.

Provides high-level operations for RSA keypair generation and retrieval,
used for HTTP Signature signing of outbound federation requests and for
publishing the public key in the ActivityPub actor document.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.models.keypair import Keypair
from app.repositories.keypair import KeypairRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class KeypairService:
    """Service for generating and retrieving the local actor's RSA keypair.

    Wraps :class:`KeypairRepository` to provide keypair lifecycle
    management including on-demand generation and convenient accessors
    for the PEM-encoded public and private keys.

    Args:
        session: The async database session used for the lifetime of
            this service instance.
    """

    _repo: KeypairRepository

    def __init__(self, session: AsyncSession) -> None:
        """Initialise the keypair service.

        Args:
            session: The async database session to use.
        """
        self._repo = KeypairRepository(session)

    async def get_or_create(self) -> tuple[str, str]:
        """Return the active keypair, generating one if none exists.

        Checks for an existing active keypair and returns it. If no
        keypair is found, a new RSA 2048-bit keypair is generated,
        stored, and returned.

        Returns:
            A tuple of ``(public_key_pem, private_key_pem)`` strings.
        """
        existing = await self._repo.get_active()
        if existing is not None:
            return existing.public_key, existing.private_key
        return await self.generate_keypair()

    async def get_public_key(self) -> str:
        """Return the PEM-encoded public key of the active keypair.

        Delegates to :meth:`get_or_create` to ensure a keypair exists.

        Returns:
            The PEM-encoded RSA public key string.
        """
        public_pem, _ = await self.get_or_create()
        return public_pem

    async def get_private_key(self) -> str:
        """Return the PEM-encoded private key of the active keypair.

        Delegates to :meth:`get_or_create` to ensure a keypair exists.

        Returns:
            The PEM-encoded RSA private key string.
        """
        _, private_pem = await self.get_or_create()
        return private_pem

    async def generate_keypair(self) -> tuple[str, str]:
        """Generate a new RSA 2048-bit keypair and persist it.

        Creates a fresh RSA keypair, stores both the public and private
        keys in PEM format via the repository, and commits the
        transaction.

        Returns:
            A tuple of ``(public_key_pem, private_key_pem)`` strings.
        """
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        public_pem = (
            private_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("utf-8")
        )

        entity = Keypair(public_key=public_pem, private_key=private_pem)
        _ = await self._repo.add(entity)
        await self._repo.commit()

        return public_pem, private_pem
