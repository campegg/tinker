"""Base model class with UUID primary key convention.

All Tinker models inherit from this base, which provides a UUID primary
key column and common column definitions.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all Tinker ORM models."""

    pass


class UUIDModel(Base):
    """Abstract base model providing a UUID primary key.

    All domain models should inherit from this class to get a consistent
    UUID primary key and created_at timestamp.
    """

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
