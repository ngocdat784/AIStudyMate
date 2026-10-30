
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.connection import SessionLocal
from app.models.ai_audit_log import AIAuditLog

logger = logging.getLogger(__name__)

MAX_ERROR_MESSAGE_LENGTH = 4000


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_error_message(exc: Exception) -> str:
    """Return a bounded error message without storing an entire traceback."""
    message = str(exc).strip() or type(exc).__name__
    return message[:MAX_ERROR_MESSAGE_LENGTH]


def start_ai_audit_log(
    *,
    analysis_type: str,
    model_name: str,
    prompt_version: str,
    input_data: dict[str, Any] | None = None,
    prompt_text: str | None = None,
) -> int | None:
    """
    Create and commit an audit record before calling the AI.

    Returns the audit ID, or None if audit storage is unavailable.
    Audit failures must not replace the original AI operation's outcome.
    """
    db: Session | None = None

    try:
        db = SessionLocal()
        record = AIAuditLog(
            analysis_type=analysis_type,
            model_name=model_name,
            prompt_version=prompt_version,
            input_data=input_data,
            prompt_text=prompt_text,
            validation_status="not_run",
            status="started",
        )

        db.add(record)
        db.commit()
        db.refresh(record)

        return record.id

    except Exception:
        if db is not None:
            db.rollback()
        logger.exception(
            "Could not create AI audit log for analysis_type=%s",
            analysis_type,
        )
        return None

    finally:
        if db is not None:
            db.close()


def finish_ai_audit_log(
    audit_id: int | None,
    *,
    status: str,
    validation_status: str,
    raw_response: str | None = None,
    validated_result: dict[str, Any] | None = None,
    error: Exception | None = None,
    duration_ms: int | None = None,
) -> bool:
    """
    Update an existing audit record after AI processing.

    Returns False if the audit record cannot be updated.
    This function deliberately does not propagate audit-storage errors.
    """
    if audit_id is None:
        return False

    allowed_statuses = {
        "success",
        "validation_failed",
        "error",
    }
    allowed_validation_statuses = {
        "not_run",
        "passed",
        "failed",
    }

    if status not in allowed_statuses:
        raise ValueError(f"Unsupported audit status: {status}")

    if validation_status not in allowed_validation_statuses:
        raise ValueError(
            f"Unsupported validation status: {validation_status}"
        )

    db: Session | None = None

    try:
        db = SessionLocal()
        record = db.scalar(
            select(AIAuditLog).where(AIAuditLog.id == audit_id)
        )

        if record is None:
            logger.warning(
                "AI audit log %s was not found during finalization",
                audit_id,
            )
            return False

        record.status = status
        record.validation_status = validation_status
        record.raw_response = raw_response
        record.validated_result = validated_result
        record.completed_at = _utc_now()

        if error is not None:
            record.error_type = type(error).__name__
            record.error_message = _safe_error_message(error)
        else:
            record.error_type = None
            record.error_message = None

        if duration_ms is not None:
            record.duration_ms = max(0, duration_ms)

        db.commit()
        return True

    except Exception:
        if db is not None:
            db.rollback()
        logger.exception(
            "Could not finalize AI audit log id=%s",
            audit_id,
        )
        return False

    finally:
        if db is not None:
            db.close()
