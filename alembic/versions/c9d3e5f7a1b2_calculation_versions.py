"""calculation versions (P1-10)

Revision ID: c9d3e5f7a1b2
Revises: b7e1c4a9d2f3
Create Date: 2026-11-17
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "c9d3e5f7a1b2"
down_revision: Union[str, Sequence[str], None] = "b7e1c4a9d2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Неизменяемая история снимков расчёта."""
    op.create_table(
        "calculation_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("calculation_id", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=50), nullable=False),
        sa.Column("quote", sa.JSON(), nullable=False),
        sa.Column("price_list_version", sa.String(length=64), nullable=True),
        sa.Column("policy_version", sa.String(length=20), nullable=True),
        sa.Column("engine_version", sa.String(length=20), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["calculation_id"], ["calculations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "calculation_id", "version_no", name="uq_calc_version_no"
        ),
    )
    op.create_index(
        "ix_calculation_versions_calculation_id",
        "calculation_versions",
        ["calculation_id"],
        unique=False,
    )
    op.create_index(
        "ix_calculation_versions_created_at",
        "calculation_versions",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_calculation_versions_created_at", table_name="calculation_versions")
    op.drop_index(
        "ix_calculation_versions_calculation_id", table_name="calculation_versions"
    )
    op.drop_table("calculation_versions")
