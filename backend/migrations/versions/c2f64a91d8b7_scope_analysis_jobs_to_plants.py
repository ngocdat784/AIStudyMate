"""scope hardware analysis jobs to plants

Revision ID: c2f64a91d8b7
Revises: b27f4a8d1c30
Create Date: 2026-11-16 09:22:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c2f64a91d8b7"
down_revision: Union[str, Sequence[str], None] = "b27f4a8d1c30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "hardware_analysis_jobs",
        sa.Column("plant_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_hardware_analysis_jobs_plant_id_monitoring_plants",
        "hardware_analysis_jobs",
        "monitoring_plants",
        ["plant_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_hardware_analysis_jobs_plant_id",
        "hardware_analysis_jobs",
        ["plant_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hardware_analysis_jobs_plant_id",
        table_name="hardware_analysis_jobs",
    )
    op.drop_constraint(
        "fk_hardware_analysis_jobs_plant_id_monitoring_plants",
        "hardware_analysis_jobs",
        type_="foreignkey",
    )
    op.drop_column("hardware_analysis_jobs", "plant_id")
