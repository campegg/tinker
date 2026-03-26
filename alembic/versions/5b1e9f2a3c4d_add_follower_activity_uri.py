"""add follow_activity_uri to followers table

Revision ID: 5b1e9f2a3c4d
Revises: 4a2d8e3f1b0c
Create Date: 2026-03-25 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5b1e9f2a3c4d"
down_revision: str | None = "4a2d8e3f1b0c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add follow_activity_uri column to the followers table."""
    op.add_column(
        "followers",
        sa.Column("follow_activity_uri", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Remove follow_activity_uri column from the followers table."""
    op.drop_column("followers", "follow_activity_uri")
