"""add persistent hardware analysis jobs

Revision ID: f13a9d8c2e41
Revises: a12f6c9d4e21
Create Date: 2026-11-11 10:46:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f13a9d8c2e41"
down_revision: Union[str, Sequence[str], None] = "a12f6c9d4e21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hardware_analysis_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column(
            "stage",
            sa.String(length=40),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column(
            "progress",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("source_file_sha256", sa.String(length=64), nullable=False),
        sa.Column("input_file", sa.LargeBinary(), nullable=True),
        sa.Column(
            "custom_prompt",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("result_data", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_hardware_analysis_jobs_status",
        ),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_hardware_analysis_jobs_progress",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["alert_users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_hardware_analysis_jobs_queue",
        "hardware_analysis_jobs",
        ["status", "available_at", "created_at"],
    )
    op.create_index(
        "ix_hardware_analysis_jobs_owner_created",
        "hardware_analysis_jobs",
        ["owner_user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hardware_analysis_jobs_owner_created",
        table_name="hardware_analysis_jobs",
    )
    op.drop_index(
        "ix_hardware_analysis_jobs_queue",
        table_name="hardware_analysis_jobs",
    )
    op.drop_table("hardware_analysis_jobs")
