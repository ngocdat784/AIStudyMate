"""add root-cause corrective action planning fields

Revision ID: 9f3c7a2d6e10
Revises: c2f64a91d8b7
Create Date: 2026-11-16 13:11:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9f3c7a2d6e10"
down_revision: Union[str, Sequence[str], None] = "c2f64a91d8b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "improvement_cases",
        sa.Column("action_priority", sa.String(length=12), nullable=True),
    )
    op.add_column(
        "improvement_cases",
        sa.Column("action_due_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "improvement_cases",
        sa.Column("expected_impact", sa.Text(), nullable=True),
    )
    op.add_column(
        "improvement_cases",
        sa.Column("verification_plan", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_improvement_cases_action_priority",
        "improvement_cases",
        "action_priority IS NULL OR action_priority IN ('high', 'medium', 'low')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_improvement_cases_action_priority",
        "improvement_cases",
        type_="check",
    )
    op.drop_column("improvement_cases", "verification_plan")
    op.drop_column("improvement_cases", "expected_impact")
    op.drop_column("improvement_cases", "action_due_at")
    op.drop_column("improvement_cases", "action_priority")
