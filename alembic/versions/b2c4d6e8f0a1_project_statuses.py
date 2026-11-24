"""project_statuses: статус импортированного проекта в БД.

Revision ID: b2c4d6e8f0a1
Revises: e7a1c3d5f9b2
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2c4d6e8f0a1"
down_revision: str | Sequence[str] | None = "e7a1c3d5f9b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_statuses",
        sa.Column("project_id", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("project_id"),
    )


def downgrade() -> None:
    op.drop_table("project_statuses")
