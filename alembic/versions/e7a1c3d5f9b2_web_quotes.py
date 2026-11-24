"""web_quotes: постоянное хранение снимков веб-расчётов.

Revision ID: e7a1c3d5f9b2
Revises: c9d3e5f7a1b2
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7a1c3d5f9b2"
down_revision: str | Sequence[str] | None = "c9d3e5f7a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_quotes",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=16), nullable=True),
        sa.Column("page_index", sa.Integer(), nullable=True),
        sa.Column("zone_type", sa.String(length=50), nullable=True),
        sa.Column("modules", sa.JSON(), nullable=True),
        sa.Column("quote", sa.JSON(), nullable=True),
        sa.Column("image_path", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_web_quotes_created_at", "web_quotes", ["created_at"])
    op.create_index("ix_web_quotes_project_id", "web_quotes", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_web_quotes_project_id", table_name="web_quotes")
    op.drop_index("ix_web_quotes_created_at", table_name="web_quotes")
    op.drop_table("web_quotes")
