"""add plant-scoped access and alert lifecycle history

Revision ID: c84f19a2d603
Revises: bc51d7a93e20
Create Date: 2026-11-09 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c84f19a2d603"
down_revision: Union[str, Sequence[str], None] = "bc51d7a93e20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alert_user_plants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("plant_id", sa.Integer(), nullable=False),
        sa.Column("assigned_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["assigned_by"], ["alert_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["plant_id"], ["monitoring_plants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["alert_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "plant_id", name="uq_alert_user_plant"),
    )
    op.create_index("ix_alert_user_plants_user_id", "alert_user_plants", ["user_id"], unique=False)
    op.create_index("ix_alert_user_plants_plant_id", "alert_user_plants", ["plant_id"], unique=False)
    op.create_index(
        "ix_alert_user_plants_plant_user",
        "alert_user_plants",
        ["plant_id", "user_id"],
        unique=False,
    )

    op.add_column(
        "monitoring_alerts",
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "alert_status_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("alert_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("event_data", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["alert_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["alert_id"], ["monitoring_alerts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_alert_status_history_alert_id",
        "alert_status_history",
        ["alert_id"],
        unique=False,
    )
    op.create_index(
        "ix_alert_status_history_actor_user_id",
        "alert_status_history",
        ["actor_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_alert_status_history_created_at",
        "alert_status_history",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_alert_status_history_alert_created",
        "alert_status_history",
        ["alert_id", "created_at"],
        unique=False,
    )
    op.execute(
        """
        INSERT INTO alert_status_history
            (alert_id, event_type, from_status, to_status, actor_user_id, note, created_at)
        SELECT id, 'detected', NULL, status, NULL,
               'Lịch sử ban đầu được tạo khi nâng cấp schema.',
               detected_at
        FROM monitoring_alerts
        """
    )

    op.add_column(
        "alert_notifications",
        sa.Column("event_key", sa.String(length=96), server_default="created", nullable=False),
    )
    op.drop_constraint(
        "uq_alert_notification_user_alert",
        "alert_notifications",
        type_="unique",
    )
    op.alter_column("alert_notifications", "event_key", server_default=None)
    op.create_unique_constraint(
        "uq_alert_notification_user_alert_event",
        "alert_notifications",
        ["user_id", "alert_id", "event_key"],
    )

    op.add_column(
        "alert_deliveries",
        sa.Column("payload", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("alert_deliveries", "payload")

    op.drop_constraint(
        "uq_alert_notification_user_alert_event",
        "alert_notifications",
        type_="unique",
    )
    op.drop_column("alert_notifications", "event_key")
    op.create_unique_constraint(
        "uq_alert_notification_user_alert",
        "alert_notifications",
        ["user_id", "alert_id"],
    )

    op.drop_index("ix_alert_status_history_alert_created", table_name="alert_status_history")
    op.drop_index("ix_alert_status_history_created_at", table_name="alert_status_history")
    op.drop_index("ix_alert_status_history_actor_user_id", table_name="alert_status_history")
    op.drop_index("ix_alert_status_history_alert_id", table_name="alert_status_history")
    op.drop_table("alert_status_history")
    op.drop_column("monitoring_alerts", "snoozed_until")

    op.drop_index("ix_alert_user_plants_plant_user", table_name="alert_user_plants")
    op.drop_index("ix_alert_user_plants_plant_id", table_name="alert_user_plants")
    op.drop_index("ix_alert_user_plants_user_id", table_name="alert_user_plants")
    op.drop_table("alert_user_plants")
