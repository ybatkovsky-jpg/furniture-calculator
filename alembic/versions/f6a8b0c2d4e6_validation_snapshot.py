"""web_quotes/web_quote_versions: снимок validation (этап 2).

Revision ID: f6a8b0c2d4e6
Revises: d4e6f8a0b2c3
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6a8b0c2d4e6"
down_revision: str | Sequence[str] | None = "d4e6f8a0b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("web_quotes", sa.Column("validation", sa.JSON(), nullable=True))
    op.add_column(
        "web_quote_versions", sa.Column("validation", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("web_quote_versions", "validation")
    op.drop_column("web_quotes", "validation")
