from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class HardwareDatasetCache(Base):
    __tablename__ = "hardware_dataset_cache"
    __table_args__ = (
        CheckConstraint(
            "byte_size > 0",
            name="ck_hardware_dataset_cache_byte_size",
        ),
        Index(
            "ix_hardware_dataset_cache_expires_created",
            "expires_at",
            "created_at",
        ),
    )

    dataset_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("alert_users.id", ondelete="CASCADE"),
        nullable=False,
    )
    payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
