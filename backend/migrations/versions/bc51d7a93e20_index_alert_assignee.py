"""index monitoring alert assignee

Revision ID: bc51d7a93e20
Revises: 9a2c6e4f1b30
Create Date: 2026-11-09 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "bc51d7a93e20"
down_revision: Union[str, Sequence[str], None] = "9a2c6e4f1b30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_monitoring_alerts_assigned_to",
        "monitoring_alerts",
        ["assigned_to"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_monitoring_alerts_assigned_to",
        table_name="monitoring_alerts",
    )
