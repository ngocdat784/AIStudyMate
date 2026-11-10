"""add Phase 12 identity, organization, lineage and report governance

Revision ID: a12f6c9d4e21
Revises: c84f19a2d603
Create Date: 2026-11-10 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a12f6c9d4e21"
down_revision: Union[str, Sequence[str], None] = "c84f19a2d603"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "alert_users",
        sa.Column("account_status", sa.String(length=20), server_default=sa.text("'active'"), nullable=False),
    )
    op.add_column(
        "alert_users",
        sa.Column("failed_login_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("alert_users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("alert_users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "factories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_factory_organization_name"),
    )
    op.create_index("ix_factories_organization_id", "factories", ["organization_id"], unique=False)

    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("factory_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["factory_id"], ["factories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("factory_id", "name", name="uq_department_factory_name"),
        sa.UniqueConstraint("id", "factory_id", name="uq_department_id_factory"),
    )
    op.create_index("ix_departments_factory_id", "departments", ["factory_id"], unique=False)

    op.add_column("monitoring_plants", sa.Column("factory_id", sa.Integer(), nullable=True))
    op.add_column("monitoring_plants", sa.Column("department_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_monitoring_plants_factory_id_factories",
        "monitoring_plants",
        "factories",
        ["factory_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_monitoring_plants_department_id_departments",
        "monitoring_plants",
        "departments",
        ["department_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_plant_department_factory",
        "monitoring_plants",
        "departments",
        ["department_id", "factory_id"],
        ["id", "factory_id"],
    )
    op.create_check_constraint(
        "ck_plant_department_requires_factory",
        "monitoring_plants",
        "department_id IS NULL OR factory_id IS NOT NULL",
    )
    op.create_index("ix_monitoring_plants_factory_id", "monitoring_plants", ["factory_id"], unique=False)
    op.create_index("ix_monitoring_plants_department_id", "monitoring_plants", ["department_id"], unique=False)

    op.create_table(
        "alert_user_factories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("factory_id", sa.Integer(), nullable=False),
        sa.Column("assigned_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["assigned_by"], ["alert_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["factory_id"], ["factories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["alert_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "factory_id", name="uq_alert_user_factory"),
    )
    op.create_index("ix_alert_user_factories_user_id", "alert_user_factories", ["user_id"], unique=False)
    op.create_index("ix_alert_user_factories_factory_id", "alert_user_factories", ["factory_id"], unique=False)
    op.create_index("ix_alert_user_factories_factory_user", "alert_user_factories", ["factory_id", "user_id"], unique=False)

    op.create_table(
        "alert_user_departments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("assigned_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["assigned_by"], ["alert_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["alert_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "department_id", name="uq_alert_user_department"),
    )
    op.create_index("ix_alert_user_departments_user_id", "alert_user_departments", ["user_id"], unique=False)
    op.create_index("ix_alert_user_departments_department_id", "alert_user_departments", ["department_id"], unique=False)
    op.create_index(
        "ix_alert_user_departments_department_user",
        "alert_user_departments",
        ["department_id", "user_id"],
        unique=False,
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["alert_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"], unique=False)
    op.create_index("ix_auth_sessions_user_expires", "auth_sessions", ["user_id", "expires_at"], unique=False)

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["alert_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"], unique=False)
    op.create_index(
        "ix_password_reset_tokens_user_expires",
        "password_reset_tokens",
        ["user_id", "expires_at"],
        unique=False,
    )

    op.create_table(
        "user_action_audits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("object_type", sa.String(length=100), nullable=False),
        sa.Column("object_id", sa.String(length=128), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("before_data", sa.JSON(), nullable=True),
        sa.Column("after_data", sa.JSON(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["alert_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_action_audits_actor_user_id", "user_action_audits", ["actor_user_id"], unique=False)
    op.create_index("ix_user_action_audits_created_at", "user_action_audits", ["created_at"], unique=False)
    op.create_index("ix_user_action_audits_actor_created", "user_action_audits", ["actor_user_id", "created_at"], unique=False)
    op.create_index("ix_user_action_audits_object", "user_action_audits", ["object_type", "object_id"], unique=False)

    op.add_column("analysis_history", sa.Column("creator_user_id", sa.Integer(), nullable=True))
    op.add_column("analysis_history", sa.Column("organization_id", sa.Integer(), nullable=True))
    op.add_column("analysis_history", sa.Column("factory_id", sa.Integer(), nullable=True))
    op.add_column("analysis_history", sa.Column("department_id", sa.Integer(), nullable=True))
    op.add_column("analysis_history", sa.Column("plant_id", sa.Integer(), nullable=True))
    op.add_column("analysis_history", sa.Column("dataset_version", sa.Integer(), nullable=True))
    op.add_column("analysis_history", sa.Column("source_file_sha256", sa.String(length=64), nullable=True))
    op.add_column("analysis_history", sa.Column("algorithm_version", sa.String(length=80), nullable=True))
    op.add_column("analysis_history", sa.Column("ai_status", sa.String(length=30), nullable=True))
    op.add_column("analysis_history", sa.Column("evidence", sa.JSON(), nullable=True))
    op.create_foreign_key("fk_analysis_history_creator_user_id_alert_users", "analysis_history", "alert_users", ["creator_user_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_analysis_history_organization_id_organizations", "analysis_history", "organizations", ["organization_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_analysis_history_factory_id_factories", "analysis_history", "factories", ["factory_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_analysis_history_department_id_departments", "analysis_history", "departments", ["department_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_analysis_history_plant_id_monitoring_plants", "analysis_history", "monitoring_plants", ["plant_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_analysis_history_creator_user_id", "analysis_history", ["creator_user_id"], unique=False)
    op.create_index("ix_analysis_history_organization_id", "analysis_history", ["organization_id"], unique=False)
    op.create_index("ix_analysis_history_factory_id", "analysis_history", ["factory_id"], unique=False)
    op.create_index("ix_analysis_history_department_id", "analysis_history", ["department_id"], unique=False)
    op.create_index("ix_analysis_history_plant_id", "analysis_history", ["plant_id"], unique=False)

    op.create_table(
        "governed_reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("factory_id", sa.Integer(), nullable=True),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("plant_id", sa.Integer(), nullable=True),
        sa.Column("creator_user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["creator_user_id"], ["alert_users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["factory_id"], ["factories.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["plant_id"], ["monitoring_plants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_governed_reports_organization_id", "governed_reports", ["organization_id"], unique=False)
    op.create_index("ix_governed_reports_factory_id", "governed_reports", ["factory_id"], unique=False)
    op.create_index("ix_governed_reports_department_id", "governed_reports", ["department_id"], unique=False)
    op.create_index("ix_governed_reports_plant_id", "governed_reports", ["plant_id"], unique=False)
    op.create_index("ix_governed_reports_creator_user_id", "governed_reports", ["creator_user_id"], unique=False)
    op.create_index("ix_governed_reports_created_at", "governed_reports", ["created_at"], unique=False)
    op.create_index(
        "ix_governed_reports_scope_created",
        "governed_reports",
        ["organization_id", "factory_id", "plant_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "governed_report_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("source_data", sa.JSON(), nullable=True),
        sa.Column("validation_status", sa.String(length=30), nullable=False),
        sa.Column("ai_status", sa.String(length=30), nullable=False),
        sa.Column("approval_status", sa.String(length=30), nullable=False),
        sa.Column("publication_status", sa.String(length=30), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("approver_user_id", sa.Integer(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["approver_user_id"], ["alert_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["alert_users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["report_id"], ["governed_reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_id", "version", name="uq_governed_report_version"),
    )
    op.create_index("ix_governed_report_versions_report_id", "governed_report_versions", ["report_id"], unique=False)
    op.create_index("ix_governed_report_versions_approval_status", "governed_report_versions", ["approval_status"], unique=False)
    op.create_index("ix_governed_report_versions_created_by", "governed_report_versions", ["created_by"], unique=False)
    op.create_index("ix_governed_report_versions_created_at", "governed_report_versions", ["created_at"], unique=False)
    op.create_index(
        "ix_governed_report_versions_status_created",
        "governed_report_versions",
        ["approval_status", "created_at"],
        unique=False,
    )

    op.create_table(
        "governed_report_edits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("report_version_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("before_data", sa.JSON(), nullable=True),
        sa.Column("after_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["alert_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["report_version_id"], ["governed_report_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_governed_report_edits_report_version_id", "governed_report_edits", ["report_version_id"], unique=False)
    op.create_index("ix_governed_report_edits_created_at", "governed_report_edits", ["created_at"], unique=False)
    op.create_index(
        "ix_governed_report_edits_version_created",
        "governed_report_edits",
        ["report_version_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_governed_report_edits_version_created", table_name="governed_report_edits")
    op.drop_index("ix_governed_report_edits_created_at", table_name="governed_report_edits")
    op.drop_index("ix_governed_report_edits_report_version_id", table_name="governed_report_edits")
    op.drop_table("governed_report_edits")

    op.drop_index("ix_governed_report_versions_status_created", table_name="governed_report_versions")
    op.drop_index("ix_governed_report_versions_created_at", table_name="governed_report_versions")
    op.drop_index("ix_governed_report_versions_created_by", table_name="governed_report_versions")
    op.drop_index("ix_governed_report_versions_approval_status", table_name="governed_report_versions")
    op.drop_index("ix_governed_report_versions_report_id", table_name="governed_report_versions")
    op.drop_table("governed_report_versions")

    op.drop_index("ix_governed_reports_scope_created", table_name="governed_reports")
    op.drop_index("ix_governed_reports_created_at", table_name="governed_reports")
    op.drop_index("ix_governed_reports_creator_user_id", table_name="governed_reports")
    op.drop_index("ix_governed_reports_plant_id", table_name="governed_reports")
    op.drop_index("ix_governed_reports_department_id", table_name="governed_reports")
    op.drop_index("ix_governed_reports_factory_id", table_name="governed_reports")
    op.drop_index("ix_governed_reports_organization_id", table_name="governed_reports")
    op.drop_table("governed_reports")

    op.drop_index("ix_analysis_history_plant_id", table_name="analysis_history")
    op.drop_index("ix_analysis_history_department_id", table_name="analysis_history")
    op.drop_index("ix_analysis_history_factory_id", table_name="analysis_history")
    op.drop_index("ix_analysis_history_organization_id", table_name="analysis_history")
    op.drop_index("ix_analysis_history_creator_user_id", table_name="analysis_history")
    op.drop_constraint("fk_analysis_history_plant_id_monitoring_plants", "analysis_history", type_="foreignkey")
    op.drop_constraint("fk_analysis_history_department_id_departments", "analysis_history", type_="foreignkey")
    op.drop_constraint("fk_analysis_history_factory_id_factories", "analysis_history", type_="foreignkey")
    op.drop_constraint("fk_analysis_history_organization_id_organizations", "analysis_history", type_="foreignkey")
    op.drop_constraint("fk_analysis_history_creator_user_id_alert_users", "analysis_history", type_="foreignkey")
    op.drop_column("analysis_history", "evidence")
    op.drop_column("analysis_history", "ai_status")
    op.drop_column("analysis_history", "algorithm_version")
    op.drop_column("analysis_history", "source_file_sha256")
    op.drop_column("analysis_history", "dataset_version")
    op.drop_column("analysis_history", "plant_id")
    op.drop_column("analysis_history", "department_id")
    op.drop_column("analysis_history", "factory_id")
    op.drop_column("analysis_history", "organization_id")
    op.drop_column("analysis_history", "creator_user_id")

    op.drop_index("ix_user_action_audits_object", table_name="user_action_audits")
    op.drop_index("ix_user_action_audits_actor_created", table_name="user_action_audits")
    op.drop_index("ix_user_action_audits_created_at", table_name="user_action_audits")
    op.drop_index("ix_user_action_audits_actor_user_id", table_name="user_action_audits")
    op.drop_table("user_action_audits")
    op.drop_index("ix_password_reset_tokens_user_expires", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_user_id", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
    op.drop_index("ix_auth_sessions_user_expires", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")

    op.drop_index("ix_alert_user_departments_department_user", table_name="alert_user_departments")
    op.drop_index("ix_alert_user_departments_department_id", table_name="alert_user_departments")
    op.drop_index("ix_alert_user_departments_user_id", table_name="alert_user_departments")
    op.drop_table("alert_user_departments")
    op.drop_index("ix_alert_user_factories_factory_user", table_name="alert_user_factories")
    op.drop_index("ix_alert_user_factories_factory_id", table_name="alert_user_factories")
    op.drop_index("ix_alert_user_factories_user_id", table_name="alert_user_factories")
    op.drop_table("alert_user_factories")

    op.drop_constraint("ck_plant_department_requires_factory", "monitoring_plants", type_="check")
    op.drop_constraint("fk_plant_department_factory", "monitoring_plants", type_="foreignkey")
    op.drop_constraint("fk_monitoring_plants_department_id_departments", "monitoring_plants", type_="foreignkey")
    op.drop_constraint("fk_monitoring_plants_factory_id_factories", "monitoring_plants", type_="foreignkey")
    op.drop_index("ix_monitoring_plants_department_id", table_name="monitoring_plants")
    op.drop_index("ix_monitoring_plants_factory_id", table_name="monitoring_plants")
    op.drop_column("monitoring_plants", "department_id")
    op.drop_column("monitoring_plants", "factory_id")

    op.drop_index("ix_departments_factory_id", table_name="departments")
    op.drop_table("departments")
    op.drop_index("ix_factories_organization_id", table_name="factories")
    op.drop_table("factories")
    op.drop_table("organizations")

    op.drop_column("alert_users", "last_login_at")
    op.drop_column("alert_users", "locked_until")
    op.drop_column("alert_users", "failed_login_attempts")
    op.drop_column("alert_users", "account_status")
