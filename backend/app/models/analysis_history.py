from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class AnalysisHistory(Base):
    __tablename__ = "analysis_history"
    __table_args__ = (
        Index("ix_analysis_history_dataset_created", "dataset_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    analysis_type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(Text)
    source_filename: Mapped[str | None] = mapped_column(String(255))
    dataset_id: Mapped[str | None] = mapped_column(String(64), index=True)
    result_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    creator_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    factory_id: Mapped[int | None] = mapped_column(
        ForeignKey("factories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    plant_id: Mapped[int | None] = mapped_column(
        ForeignKey("monitoring_plants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    dataset_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    algorithm_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ai_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    evidence: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

