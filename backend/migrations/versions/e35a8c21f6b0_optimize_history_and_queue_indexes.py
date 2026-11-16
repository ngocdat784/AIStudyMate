"""optimize history and queue indexes

Revision ID: e35a8c21f6b0
Revises: d44f8c31a260
Create Date: 2026-11-11 12:12:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e35a8c21f6b0"
down_revision: Union[str, Sequence[str], None] = "d44f8c31a260"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_analysis_history_creator_created_id",
        "analysis_history",
        ["creator_user_id", "created_at", "id"],
    )
    op.create_index(
        "ix_analysis_history_type_created_id",
        "analysis_history",
        ["analysis_type", "created_at", "id"],
    )
    op.create_index(
        "ix_analysis_history_created_id",
        "analysis_history",
        ["created_at", "id"],
    )

    op.drop_index(
        "ix_hardware_analysis_jobs_owner_created",
        table_name="hardware_analysis_jobs",
    )
    op.create_index(
        "ix_hardware_analysis_jobs_owner_created_id",
        "hardware_analysis_jobs",
        ["owner_user_id", "created_at", "id"],
    )
    op.create_index(
        "ix_hardware_analysis_jobs_created_id",
        "hardware_analysis_jobs",
        ["created_at", "id"],
    )

    op.drop_index(
        "ix_integration_sync_runs_connector_created",
        table_name="integration_sync_runs",
    )
    op.create_index(
        "ix_integration_sync_runs_connector_created_id",
        "integration_sync_runs",
        ["connector_id", "created_at", "id"],
    )
    op.create_index(
        "ix_integration_sync_runs_created_id",
        "integration_sync_runs",
        ["created_at", "id"],
    )

    op.drop_index(
        "ix_improvement_cases_plant_created",
        table_name="improvement_cases",
    )
    op.drop_index(
        "ix_improvement_cases_plant_status",
        table_name="improvement_cases",
    )
    op.create_index(
        "ix_improvement_cases_plant_created_id",
        "improvement_cases",
        ["plant_id", "created_at", "id"],
    )
    op.create_index(
        "ix_improvement_cases_plant_status_created_id",
        "improvement_cases",
        ["plant_id", "status", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_improvement_cases_plant_status_created_id",
        table_name="improvement_cases",
    )
    op.drop_index(
        "ix_improvement_cases_plant_created_id",
        table_name="improvement_cases",
    )
    op.create_index(
        "ix_improvement_cases_plant_status",
        "improvement_cases",
        ["plant_id", "status"],
    )
    op.create_index(
        "ix_improvement_cases_plant_created",
        "improvement_cases",
        ["plant_id", "created_at"],
    )

    op.drop_index(
        "ix_integration_sync_runs_created_id",
        table_name="integration_sync_runs",
    )
    op.drop_index(
        "ix_integration_sync_runs_connector_created_id",
        table_name="integration_sync_runs",
    )
    op.create_index(
        "ix_integration_sync_runs_connector_created",
        "integration_sync_runs",
        ["connector_id", "created_at"],
    )

    op.drop_index(
        "ix_hardware_analysis_jobs_created_id",
        table_name="hardware_analysis_jobs",
    )
    op.drop_index(
        "ix_hardware_analysis_jobs_owner_created_id",
        table_name="hardware_analysis_jobs",
    )
    op.create_index(
        "ix_hardware_analysis_jobs_owner_created",
        "hardware_analysis_jobs",
        ["owner_user_id", "created_at"],
    )

    op.drop_index(
        "ix_analysis_history_created_id",
        table_name="analysis_history",
    )
    op.drop_index(
        "ix_analysis_history_type_created_id",
        table_name="analysis_history",
    )
    op.drop_index(
        "ix_analysis_history_creator_created_id",
        table_name="analysis_history",
    )
