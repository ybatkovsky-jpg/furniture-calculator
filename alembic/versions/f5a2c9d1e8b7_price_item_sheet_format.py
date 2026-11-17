"""price_item sheet format (P1-05)

Revision ID: f5a2c9d1e8b7
Revises: 123c453dd1ed
Create Date: 2026-11-17
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "f5a2c9d1e8b7"
down_revision: Union[str, Sequence[str], None] = "123c453dd1ed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Формат листа для позиций с ценой за лист (МДФ-фасады)."""
    with op.batch_alter_table("price_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sheet_width_mm", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("sheet_height_mm", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("price_items", schema=None) as batch_op:
        batch_op.drop_column("sheet_height_mm")
        batch_op.drop_column("sheet_width_mm")
