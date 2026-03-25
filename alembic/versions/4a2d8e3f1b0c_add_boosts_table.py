"""add boosts table

Revision ID: 4a2d8e3f1b0c
Revises: d630cad55ef3
Create Date: 2026-03-24 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4a2d8e3f1b0c"
down_revision: str | None = "d630cad55ef3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the boosts table for tracking outgoing Announce activities."""
    op.create_table(
        "boosts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("note_uri", sa.Text(), nullable=False),
        sa.Column("actor_uri", sa.Text(), nullable=True),
        sa.Column("activity_uri", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("activity_uri"),
    )


def downgrade() -> None:
    """Drop the boosts table."""
    op.drop_table("boosts")
