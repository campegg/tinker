"""Keypair model for storing the local actor's cryptographic keys.

The keypair is used for HTTP Signature signing of outbound federation
requests and for publishing the public key in the actor document.
"""

from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import UUIDModel


class Keypair(UUIDModel):
    """Stores the local actor's RSA keypair for HTTP Signatures.

    Attributes:
        id: UUID primary key.
        created_at: Timestamp when the keypair was created.
        public_key: PEM-encoded RSA public key.
        private_key: PEM-encoded RSA private key.
    """

    __tablename__ = "keypairs"

    public_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    private_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    def __repr__(self) -> str:
        """Return a developer-friendly string representation."""
        return f"<Keypair id={self.id}>"
