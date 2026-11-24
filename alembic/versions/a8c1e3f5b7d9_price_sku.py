"""price item SKU (этап 5)

Revision ID: a8c1e3f5b7d9
Revises: f6a8b0c2d4e6
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a8c1e3f5b7d9"
down_revision: str | Sequence[str] | None = "f6a8b0c2d4e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Артикул поставщика для каждой позиции прайса."""
    with op.batch_alter_table("price_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sku", sa.String(length=100), nullable=True))
        batch_op.create_index("ix_price_items_sku", ["sku"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("price_items", schema=None) as batch_op:
        batch_op.drop_index("ix_price_items_sku")
        batch_op.drop_column("sku")
