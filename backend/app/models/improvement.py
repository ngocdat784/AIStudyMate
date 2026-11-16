from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ImprovementCase(Base):
    __tablename__ = "improvement_cases"
    __table_args__ = (
        CheckConstraint(
            "status IN ('detected', 'action_planned', 'implemented', "
            "'measured', 'evaluated', 'closed')",
            name="ck_improvement_cases_status",
        ),
        Index(
            "ix_improvement_cases_plant_created_id",
            "plant_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_improvement_cases_plant_status_created_id",
            "plant_id",
            "status",
            "created_at",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("monitoring_plants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    line_id: Mapped[int | None] = mapped_column(
        ForeignKey("monitoring_lines.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_alert_id: Mapped[int | None] = mapped_column(
        ForeignKey("monitoring_alerts.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("alert_users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    baseline: Mapped[dict] = mapped_column(JSON, nullable=False)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str | None] = mapped_column(Text, nullable=True)
    implemented_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    after_measurement: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confounders: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="detected",
        server_default="detected",
    )
    closed_at: Mapped[datetime | None] = mapped_column(
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
