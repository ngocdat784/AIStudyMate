"""persist merged multi-file datasets across API replicas

Revision ID: b27f4a8d1c30
Revises: d8a4c73e19b2
Create Date: 2026-11-11 13:55:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b27f4a8d1c30"
down_revision: Union[str, Sequence[str], None] = "d8a4c73e19b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hardware_dataset_cache",
        sa.Column("dataset_id", sa.String(length=32), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["alert_users.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "byte_size > 0",
            name="ck_hardware_dataset_cache_byte_size",
        ),
        sa.PrimaryKeyConstraint("dataset_id"),
    )
    op.create_index(
        "ix_hardware_dataset_cache_expires_created",
        "hardware_dataset_cache",
        ["expires_at", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hardware_dataset_cache_expires_created",
        table_name="hardware_dataset_cache",
    )
    op.drop_table("hardware_dataset_cache")
