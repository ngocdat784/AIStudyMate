from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class IntegrationConnector(Base):
    __tablename__ = "integration_connectors"
    __table_args__ = (
        CheckConstraint(
            "connector_type = 'file'",
            name="ck_integration_connectors_supported_type",
        ),
        Index(
            "ix_integration_connectors_plant_enabled",
            "plant_id",
            "enabled",
        ),
        Index(
            "ix_integration_connectors_owner_created",
            "owner_user_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    connector_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="file",
        server_default=text("'file'"),
    )
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("monitoring_plants.id", ondelete="CASCADE"),
        nullable=False,
    )
    line_id: Mapped[int | None] = mapped_column(
        ForeignKey("monitoring_lines.id", ondelete="SET NULL"),
        nullable=True,
    )
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("alert_users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    column_mapping: Mapped[dict[str, str]] = mapped_column(
        JSON,
        nullable=False,
    )
    normalization: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    enabled: Mapped[bool] = mapped_column(
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class IntegrationSyncRun(Base):
    __tablename__ = "integration_sync_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'duplicate')",
            name="ck_integration_sync_runs_status",
        ),
        Index(
            "ix_integration_sync_runs_connector_created_id",
            "connector_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_integration_sync_runs_owner_created",
            "owner_user_id",
            "created_at",
        ),
        Index(
            "ix_integration_sync_runs_created_id",
            "created_at",
            "id",
        ),
        Index(
            "uq_integration_sync_runs_success_idempotency",
            "connector_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("status = 'succeeded'"),
            sqlite_where=text("status = 'succeeded'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    connector_id: Mapped[str] = mapped_column(
        ForeignKey("integration_connectors.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("alert_users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    rows_read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_imported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    lineage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("monitoring_datasets.id", ondelete="SET NULL"),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
