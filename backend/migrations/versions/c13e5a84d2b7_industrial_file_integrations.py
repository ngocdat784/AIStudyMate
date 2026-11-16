"""add industrial file integration registry and lineage

Revision ID: c13e5a84d2b7
Revises: f13a9d8c2e41
Create Date: 2026-11-11 11:22:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c13e5a84d2b7"
down_revision: Union[str, Sequence[str], None] = "f13a9d8c2e41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "integration_connectors",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "connector_type",
            sa.String(length=30),
            server_default=sa.text("'file'"),
            nullable=False,
        ),
        sa.Column("plant_id", sa.Integer(), nullable=False),
        sa.Column("line_id", sa.Integer(), nullable=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("column_mapping", sa.JSON(), nullable=False),
        sa.Column(
            "normalization",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
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
            "connector_type = 'file'",
            name="ck_integration_connectors_supported_type",
        ),
        sa.ForeignKeyConstraint(
            ["line_id"],
            ["monitoring_lines.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["alert_users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["plant_id"],
            ["monitoring_plants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_integration_connectors_plant_enabled",
        "integration_connectors",
        ["plant_id", "enabled"],
    )
    op.create_index(
        "ix_integration_connectors_owner_created",
        "integration_connectors",
        ["owner_user_id", "created_at"],
    )

    op.create_table(
        "integration_sync_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("connector_id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("source_file_sha256", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column(
            "rows_read",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "rows_imported",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "rows_rejected",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("validation_report", sa.JSON(), nullable=True),
        sa.Column("lineage", sa.JSON(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'duplicate')",
            name="ck_integration_sync_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            ["integration_connectors.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["monitoring_datasets.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["alert_users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_integration_sync_runs_connector_created",
        "integration_sync_runs",
        ["connector_id", "created_at"],
    )
    op.create_index(
        "ix_integration_sync_runs_owner_created",
        "integration_sync_runs",
        ["owner_user_id", "created_at"],
    )
    op.create_index(
        "uq_integration_sync_runs_success_idempotency",
        "integration_sync_runs",
        ["connector_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("status = 'succeeded'"),
        sqlite_where=sa.text("status = 'succeeded'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_integration_sync_runs_success_idempotency",
        table_name="integration_sync_runs",
    )
    op.drop_index(
        "ix_integration_sync_runs_owner_created",
        table_name="integration_sync_runs",
    )
    op.drop_index(
        "ix_integration_sync_runs_connector_created",
        table_name="integration_sync_runs",
    )
    op.drop_table("integration_sync_runs")
    op.drop_index(
        "ix_integration_connectors_owner_created",
        table_name="integration_connectors",
    )
    op.drop_index(
        "ix_integration_connectors_plant_enabled",
        table_name="integration_connectors",
    )
    op.drop_table("integration_connectors")
