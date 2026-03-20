"""Settings model for runtime-configurable key-value pairs.

Stores identity and content settings (display name, bio, avatar, links)
that are editable through the admin interface at runtime.
"""

from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import UUIDModel


class Settings(UUIDModel):
    """Key-value settings stored in the database.

    Used for identity and content configuration that can be changed at
    runtime through the admin interface, as opposed to environment
    variables which are loaded at startup and remain immutable.

    Attributes:
        id: UUID primary key (inherited from UUIDModel).
        created_at: Timestamp of record creation (inherited from UUIDModel).
        key: Unique setting name (e.g. "display_name", "bio").
        value: Setting value, or None if unset.
    """

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(
        Text,
        unique=True,
        nullable=False,
    )
    value: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )

    def __repr__(self) -> str:
        """Return a developer-friendly string representation."""
        return f"<Settings key={self.key!r} value={self.value!r}>"
