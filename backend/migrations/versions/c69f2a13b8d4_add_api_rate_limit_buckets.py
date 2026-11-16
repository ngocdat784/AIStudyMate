"""add PostgreSQL-backed API rate-limit buckets

Revision ID: c69f2a13b8d4
Revises: e35a8c21f6b0
Create Date: 2026-11-11 12:31:49.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c69f2a13b8d4"
down_revision: Union[str, Sequence[str], None] = "e35a8c21f6b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_rate_limit_buckets",
        sa.Column("bucket_key", sa.String(length=64), nullable=False),
        sa.Column("window_start", sa.Integer(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("bucket_key"),
    )
    op.create_index(
        "ix_api_rate_limit_buckets_expires_at",
        "api_rate_limit_buckets",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_api_rate_limit_buckets_expires_at",
        table_name="api_rate_limit_buckets",
    )
    op.drop_table("api_rate_limit_buckets")
