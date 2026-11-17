"""calculation recognition confidence (P1-13)

Revision ID: b7e1c4a9d2f3
Revises: f5a2c9d1e8b7
Create Date: 2026-11-17
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "b7e1c4a9d2f3"
down_revision: Union[str, Sequence[str], None] = "f5a2c9d1e8b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Уверенность распознавания и quality_score у расчёта."""
    with op.batch_alter_table("calculations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("recognition_confidence", sa.String(length=20), nullable=True)
        )
        batch_op.add_column(sa.Column("quality_score", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("calculations", schema=None) as batch_op:
        batch_op.drop_column("quality_score")
        batch_op.drop_column("recognition_confidence")
