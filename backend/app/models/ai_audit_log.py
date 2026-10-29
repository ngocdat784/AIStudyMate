
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class AIAuditLog(Base):
    __tablename__ = "ai_audit_logs"

    __table_args__ = (
        Index(
            "ix_ai_audit_logs_type_created",
            "analysis_type",
            "created_at",
        ),
        Index(
            "ix_ai_audit_logs_status_created",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    analysis_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    model_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    prompt_version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    input_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    prompt_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    raw_response: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    validated_result: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    validation_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="not_run",
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="started",
    )

    error_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
