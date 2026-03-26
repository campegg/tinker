"""add timeline original_object_uri index

Revision ID: 58623114c17e
Revises: 5b1e9f2a3c4d
Create Date: 2026-03-26 22:40:29.534025+00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "58623114c17e"
down_revision: str | None = "5b1e9f2a3c4d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        op.f("ix_timeline_items_original_object_uri"),
        "timeline_items",
        ["original_object_uri"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_timeline_items_original_object_uri"),
        table_name="timeline_items",
    )
