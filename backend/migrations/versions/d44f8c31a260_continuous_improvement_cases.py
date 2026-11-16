"""add continuous improvement cases

Revision ID: d44f8c31a260
Revises: c13e5a84d2b7
Create Date: 2026-11-11 12:09:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d44f8c31a260"
down_revision: Union[str, Sequence[str], None] = "c13e5a84d2b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "improvement_cases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plant_id", sa.Integer(), nullable=False),
        sa.Column("line_id", sa.Integer(), nullable=True),
        sa.Column("source_alert_id", sa.Integer(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("problem", sa.Text(), nullable=False),
        sa.Column("baseline", sa.JSON(), nullable=False),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("implemented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("after_measurement", sa.JSON(), nullable=True),
        sa.Column(
            "confounders",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("conclusion", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'detected'"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('detected', 'action_planned', 'implemented', "
            "'measured', 'evaluated', 'closed')",
            name="ck_improvement_cases_status",
        ),
        sa.ForeignKeyConstraint(
            ["plant_id"],
            ["monitoring_plants.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["line_id"],
            ["monitoring_lines.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_alert_id"],
            ["monitoring_alerts.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["alert_users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["alert_users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_improvement_cases_plant_created",
        "improvement_cases",
        ["plant_id", "created_at"],
    )
    op.create_index(
        "ix_improvement_cases_plant_status",
        "improvement_cases",
        ["plant_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_improvement_cases_plant_status",
        table_name="improvement_cases",
    )
    op.drop_index(
        "ix_improvement_cases_plant_created",
        table_name="improvement_cases",
    )
    op.drop_table("improvement_cases")
