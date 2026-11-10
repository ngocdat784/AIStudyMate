from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class AlertUser(Base):
    __tablename__ = "alert_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(20), default="viewer", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    email_notifications: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    account_status: Mapped[str] = mapped_column(
        String(20), default="active", server_default=text("'active'")
    )
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user_expires", "user_id", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("alert_users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        Index("ix_password_reset_tokens_user_expires", "user_id", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("alert_users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class UserActionAudit(Base):
    __tablename__ = "user_action_audits"
    __table_args__ = (
        Index("ix_user_action_audits_actor_created", "actor_user_id", "created_at"),
        Index("ix_user_action_audits_object", "object_type", "object_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(100))
    object_type: Mapped[str] = mapped_column(String(100))
    object_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(String(30), default="api")
    outcome: Mapped[str] = mapped_column(String(20))
    before_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(180), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Factory(Base):
    __tablename__ = "factories"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "name", name="uq_factory_organization_name"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(180))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("factory_id", "name", name="uq_department_factory_name"),
        UniqueConstraint("id", "factory_id", name="uq_department_id_factory"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    factory_id: Mapped[int] = mapped_column(
        ForeignKey("factories.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(180))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AlertUserFactory(Base):
    __tablename__ = "alert_user_factories"
    __table_args__ = (
        UniqueConstraint("user_id", "factory_id", name="uq_alert_user_factory"),
        Index("ix_alert_user_factories_factory_user", "factory_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("alert_users.id", ondelete="CASCADE"), index=True
    )
    factory_id: Mapped[int] = mapped_column(
        ForeignKey("factories.id", ondelete="CASCADE"), index=True
    )
    assigned_by: Mapped[int | None] = mapped_column(
        ForeignKey("alert_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AlertUserDepartment(Base):
    __tablename__ = "alert_user_departments"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "department_id", name="uq_alert_user_department"
        ),
        Index(
            "ix_alert_user_departments_department_user",
            "department_id",
            "user_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("alert_users.id", ondelete="CASCADE"), index=True
    )
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"), index=True
    )
    assigned_by: Mapped[int | None] = mapped_column(
        ForeignKey("alert_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AlertUserPlant(Base):
    __tablename__ = "alert_user_plants"
    __table_args__ = (
        UniqueConstraint("user_id", "plant_id", name="uq_alert_user_plant"),
        Index("ix_alert_user_plants_plant_user", "plant_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("alert_users.id", ondelete="CASCADE"), index=True
    )
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("monitoring_plants.id", ondelete="CASCADE"), index=True
    )
    assigned_by: Mapped[int | None] = mapped_column(
        ForeignKey("alert_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Plant(Base):
    __tablename__ = "monitoring_plants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["department_id", "factory_id"],
            ["departments.id", "departments.factory_id"],
            name="fk_plant_department_factory",
        ),
        CheckConstraint(
            "department_id IS NULL OR factory_id IS NOT NULL",
            name="ck_plant_department_requires_factory",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    name: Mapped[str] = mapped_column(
        String(160),
        unique=True,
    )
    timezone: Mapped[str] = mapped_column(
        String(80),
        default="UTC",
    )
    factory_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "factories.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "departments.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProductionLine(Base):
    __tablename__ = "monitoring_lines"
    __table_args__ = (
        UniqueConstraint("plant_id", "name", name="uq_monitoring_line_plant_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("monitoring_plants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MonitoringDataset(Base):
    __tablename__ = "monitoring_datasets"
    __table_args__ = (
        Index("ix_monitoring_datasets_scope_enabled", "plant_id", "line_id", "enabled"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("monitoring_plants.id", ondelete="CASCADE"), index=True
    )
    line_id: Mapped[int | None] = mapped_column(
        ForeignKey("monitoring_lines.id", ondelete="SET NULL"), nullable=True, index=True
    )
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("alert_users.id"))
    granularity: Mapped[str] = mapped_column(String(10), default="day")
    schedule_interval: Mapped[int] = mapped_column(Integer, default=1)
    schedule_unit: Mapped[str] = mapped_column(String(10), default="days")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    data_rows: Mapped[list[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("monitoring_plants.id", ondelete="CASCADE"), index=True
    )
    line_id: Mapped[int | None] = mapped_column(
        ForeignKey("monitoring_lines.id", ondelete="CASCADE"), nullable=True, index=True
    )
    product: Mapped[str | None] = mapped_column(String(200), nullable=True)
    metric: Mapped[str] = mapped_column(String(30))
    operator: Mapped[str] = mapped_column(String(10))
    threshold: Mapped[float] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(20), default="high")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("alert_users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Alert(Base):
    __tablename__ = "monitoring_alerts"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_monitoring_alert_dedupe_key"),
        Index("ix_monitoring_alerts_status_detected", "status", "detected_at"),
        Index("ix_monitoring_alerts_plant_status", "plant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dedupe_key: Mapped[str] = mapped_column(String(64))
    alert_type: Mapped[str] = mapped_column(String(30))
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="SET NULL"), nullable=True
    )
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("monitoring_plants.id", ondelete="CASCADE"), index=True
    )
    line_id: Mapped[int | None] = mapped_column(
        ForeignKey("monitoring_lines.id", ondelete="SET NULL"), nullable=True
    )
    metric: Mapped[str] = mapped_column(String(30))
    scope_type: Mapped[str] = mapped_column(String(30))
    scope_value: Mapped[str | None] = mapped_column(String(200), nullable=True)
    severity: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(240))
    summary: Mapped[str] = mapped_column(Text)
    ai_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_explanation_status: Mapped[str] = mapped_column(
        String(20), default="not_configured"
    )
    observed_value: Mapped[float] = mapped_column(Float)
    threshold_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    impact: Mapped[dict] = mapped_column(JSON)
    evidence: Mapped[list[dict]] = mapped_column(JSON)
    recommended_check: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    snoozed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    feedback: Mapped[str | None] = mapped_column(String(20), nullable=True)
    feedback_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_to: Mapped[int | None] = mapped_column(
        ForeignKey("alert_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AlertNotification(Base):
    __tablename__ = "alert_notifications"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "alert_id",
            "event_key",
            name="uq_alert_notification_user_alert_event",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("alert_users.id", ondelete="CASCADE"), index=True
    )
    alert_id: Mapped[int] = mapped_column(
        ForeignKey("monitoring_alerts.id", ondelete="CASCADE"), index=True
    )
    event_key: Mapped[str] = mapped_column(String(96), default="created")
    message: Mapped[str] = mapped_column(String(300))
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AlertDelivery(Base):
    __tablename__ = "alert_deliveries"
    __table_args__ = (
        UniqueConstraint("delivery_key", name="uq_alert_delivery_key"),
        Index("ix_alert_deliveries_status_next", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    delivery_key: Mapped[str] = mapped_column(String(96))
    alert_id: Mapped[int] = mapped_column(
        ForeignKey("monitoring_alerts.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    recipient_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AlertStatusHistory(Base):
    __tablename__ = "alert_status_history"
    __table_args__ = (
        Index("ix_alert_status_history_alert_created", "alert_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(
        ForeignKey("monitoring_alerts.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(30))
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class GovernedReport(Base):
    __tablename__ = "governed_reports"
    __table_args__ = (
        Index(
            "ix_governed_reports_scope_created",
            "organization_id",
            "factory_id",
            "plant_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    factory_id: Mapped[int | None] = mapped_column(
        ForeignKey("factories.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    plant_id: Mapped[int | None] = mapped_column(
        ForeignKey("monitoring_plants.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    creator_user_id: Mapped[int] = mapped_column(
        ForeignKey("alert_users.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(240))
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class GovernedReportVersion(Base):
    __tablename__ = "governed_report_versions"
    __table_args__ = (
        UniqueConstraint(
            "report_id", "version", name="uq_governed_report_version"
        ),
        Index(
            "ix_governed_report_versions_status_created",
            "approval_status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("governed_reports.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSON)
    source_data: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    validation_status: Mapped[str] = mapped_column(
        String(30), default="not_validated"
    )
    ai_status: Mapped[str] = mapped_column(String(30), default="not_used")
    approval_status: Mapped[str] = mapped_column(
        String(30), default="draft", index=True
    )
    publication_status: Mapped[str] = mapped_column(
        String(30), default="unpublished"
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("alert_users.id", ondelete="RESTRICT"), index=True
    )
    approver_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_users.id", ondelete="SET NULL"), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class GovernedReportEdit(Base):
    __tablename__ = "governed_report_edits"
    __table_args__ = (
        Index(
            "ix_governed_report_edits_version_created",
            "report_version_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_version_id: Mapped[int] = mapped_column(
        ForeignKey("governed_report_versions.id", ondelete="CASCADE"), index=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_users.id", ondelete="SET NULL"), nullable=True
    )
    before_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
