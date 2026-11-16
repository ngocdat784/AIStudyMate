"""add AI token usage and estimated cost accounting

Revision ID: d8a4c73e19b2
Revises: c69f2a13b8d4
Create Date: 2026-11-11 12:52:22.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8a4c73e19b2"
down_revision: Union[str, Sequence[str], None] = "c69f2a13b8d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_audit_logs",
        sa.Column("input_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "ai_audit_logs",
        sa.Column("output_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "ai_audit_logs",
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_audit_logs", "estimated_cost_usd")
    op.drop_column("ai_audit_logs", "output_tokens")
    op.drop_column("ai_audit_logs", "input_tokens")
