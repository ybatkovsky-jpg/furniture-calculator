"""web_quote_versions: история редакций веб-расчёта.

Revision ID: d4e6f8a0b2c3
Revises: b2c4d6e8f0a1
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e6f8a0b2c3"
down_revision: str | Sequence[str] | None = "b2c4d6e8f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_quote_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.String(length=32), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("modules", sa.JSON(), nullable=True),
        sa.Column("quote", sa.JSON(), nullable=True),
        sa.Column("reason", sa.String(length=50), nullable=False),
        sa.Column("created_by", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_web_quote_versions_id", "web_quote_versions", ["id"])
    op.create_index("ix_web_quote_versions_quote_id", "web_quote_versions", ["quote_id"])
    op.create_index(
        "ix_web_quote_versions_created_at", "web_quote_versions", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_web_quote_versions_created_at", table_name="web_quote_versions")
    op.drop_index("ix_web_quote_versions_quote_id", table_name="web_quote_versions")
    op.drop_index("ix_web_quote_versions_id", table_name="web_quote_versions")
    op.drop_table("web_quote_versions")
