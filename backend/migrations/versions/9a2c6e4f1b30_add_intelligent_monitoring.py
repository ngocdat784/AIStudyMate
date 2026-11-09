"""add intelligent monitoring and alert management

Revision ID: 9a2c6e4f1b30
Revises: 6e0e496b7ba5
Create Date: 2026-11-02 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9a2c6e4f1b30"
down_revision: Union[str, Sequence[str], None] = "6e0e496b7ba5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alert_users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("email_notifications", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alert_users_email", "alert_users", ["email"], unique=True)
    op.create_index("ix_alert_users_role", "alert_users", ["role"], unique=False)

    op.create_table(
        "monitoring_plants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("timezone", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "monitoring_lines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("plant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["plant_id"], ["monitoring_plants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plant_id", "name", name="uq_monitoring_line_plant_name"),
    )
    op.create_index("ix_monitoring_lines_plant_id", "monitoring_lines", ["plant_id"], unique=False)

    op.create_table(
        "monitoring_datasets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("plant_id", sa.Integer(), nullable=False),
        sa.Column("line_id", sa.Integer(), nullable=True),
        sa.Column("uploaded_by", sa.Integer(), nullable=False),
        sa.Column("granularity", sa.String(length=10), nullable=False),
        sa.Column("schedule_interval", sa.Integer(), nullable=False),
        sa.Column("schedule_unit", sa.String(length=10), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_rows", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["line_id"], ["monitoring_lines.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["plant_id"], ["monitoring_plants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["alert_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_monitoring_datasets_created_at", "monitoring_datasets", ["created_at"], unique=False)
    op.create_index("ix_monitoring_datasets_line_id", "monitoring_datasets", ["line_id"], unique=False)
    op.create_index("ix_monitoring_datasets_plant_id", "monitoring_datasets", ["plant_id"], unique=False)
    op.create_index("ix_monitoring_datasets_scope_enabled", "monitoring_datasets", ["plant_id", "line_id", "enabled"], unique=False)

    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("plant_id", sa.Integer(), nullable=False),
        sa.Column("line_id", sa.Integer(), nullable=True),
        sa.Column("product", sa.String(length=200), nullable=True),
        sa.Column("metric", sa.String(length=30), nullable=False),
        sa.Column("operator", sa.String(length=10), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["alert_users.id"]),
        sa.ForeignKeyConstraint(["line_id"], ["monitoring_lines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plant_id"], ["monitoring_plants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alert_rules_line_id", "alert_rules", ["line_id"], unique=False)
    op.create_index("ix_alert_rules_plant_id", "alert_rules", ["plant_id"], unique=False)

    op.create_table(
        "monitoring_alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("alert_type", sa.String(length=30), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=True),
        sa.Column("plant_id", sa.Integer(), nullable=False),
        sa.Column("line_id", sa.Integer(), nullable=True),
        sa.Column("metric", sa.String(length=30), nullable=False),
        sa.Column("scope_type", sa.String(length=30), nullable=False),
        sa.Column("scope_value", sa.String(length=200), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("ai_explanation", sa.Text(), nullable=True),
        sa.Column("ai_explanation_status", sa.String(length=20), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=True),
        sa.Column("impact", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("recommended_check", sa.Text(), nullable=False),
        sa.Column("confidence", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("feedback", sa.String(length=20), nullable=True),
        sa.Column("feedback_note", sa.Text(), nullable=True),
        sa.Column("assigned_to", sa.Integer(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["assigned_to"], ["alert_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["line_id"], ["monitoring_lines.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["plant_id"], ["monitoring_plants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rule_id"], ["alert_rules.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_monitoring_alert_dedupe_key"),
    )
    op.create_index("ix_monitoring_alerts_detected_at", "monitoring_alerts", ["detected_at"], unique=False)
    op.create_index("ix_monitoring_alerts_plant_id", "monitoring_alerts", ["plant_id"], unique=False)
    op.create_index("ix_monitoring_alerts_plant_status", "monitoring_alerts", ["plant_id", "status"], unique=False)
    op.create_index("ix_monitoring_alerts_status", "monitoring_alerts", ["status"], unique=False)
    op.create_index("ix_monitoring_alerts_status_detected", "monitoring_alerts", ["status", "detected_at"], unique=False)

    op.create_table(
        "alert_notifications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("alert_id", sa.Integer(), nullable=False),
        sa.Column("message", sa.String(length=300), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["alert_id"], ["monitoring_alerts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["alert_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "alert_id", name="uq_alert_notification_user_alert"),
    )
    op.create_index("ix_alert_notifications_alert_id", "alert_notifications", ["alert_id"], unique=False)
    op.create_index("ix_alert_notifications_user_id", "alert_notifications", ["user_id"], unique=False)

    op.create_table(
        "alert_deliveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("delivery_key", sa.String(length=96), nullable=False),
        sa.Column("alert_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("recipient_user_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["alert_id"], ["monitoring_alerts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["alert_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_key", name="uq_alert_delivery_key"),
    )
    op.create_index("ix_alert_deliveries_alert_id", "alert_deliveries", ["alert_id"], unique=False)
    op.create_index("ix_alert_deliveries_recipient_user_id", "alert_deliveries", ["recipient_user_id"], unique=False)
    op.create_index("ix_alert_deliveries_status", "alert_deliveries", ["status"], unique=False)
    op.create_index("ix_alert_deliveries_status_next", "alert_deliveries", ["status", "next_attempt_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_alert_deliveries_status_next", table_name="alert_deliveries")
    op.drop_index("ix_alert_deliveries_status", table_name="alert_deliveries")
    op.drop_index("ix_alert_deliveries_recipient_user_id", table_name="alert_deliveries")
    op.drop_index("ix_alert_deliveries_alert_id", table_name="alert_deliveries")
    op.drop_table("alert_deliveries")
    op.drop_index("ix_alert_notifications_user_id", table_name="alert_notifications")
    op.drop_index("ix_alert_notifications_alert_id", table_name="alert_notifications")
    op.drop_table("alert_notifications")
    op.drop_index("ix_monitoring_alerts_status_detected", table_name="monitoring_alerts")
    op.drop_index("ix_monitoring_alerts_status", table_name="monitoring_alerts")
    op.drop_index("ix_monitoring_alerts_plant_status", table_name="monitoring_alerts")
    op.drop_index("ix_monitoring_alerts_plant_id", table_name="monitoring_alerts")
    op.drop_index("ix_monitoring_alerts_detected_at", table_name="monitoring_alerts")
    op.drop_table("monitoring_alerts")
    op.drop_index("ix_alert_rules_plant_id", table_name="alert_rules")
    op.drop_index("ix_alert_rules_line_id", table_name="alert_rules")
    op.drop_table("alert_rules")
    op.drop_index("ix_monitoring_datasets_scope_enabled", table_name="monitoring_datasets")
    op.drop_index("ix_monitoring_datasets_plant_id", table_name="monitoring_datasets")
    op.drop_index("ix_monitoring_datasets_line_id", table_name="monitoring_datasets")
    op.drop_index("ix_monitoring_datasets_created_at", table_name="monitoring_datasets")
    op.drop_table("monitoring_datasets")
    op.drop_index("ix_monitoring_lines_plant_id", table_name="monitoring_lines")
    op.drop_table("monitoring_lines")
    op.drop_table("monitoring_plants")
    op.drop_index("ix_alert_users_role", table_name="alert_users")
    op.drop_index("ix_alert_users_email", table_name="alert_users")
    op.drop_table("alert_users")
