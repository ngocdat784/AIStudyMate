from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.database.connection import engine, get_db
from app.models import AIAuditLog, HardwareAnalysisJob, IntegrationSyncRun
from app.services.hardware_analysis_jobs import get_hardware_analysis_worker_status
from app.services.monitoring_auth import require_roles
from app.services.monitoring_scheduler import get_monitoring_scheduler_status
from app.services.observability import request_metrics

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Operational Observability"])


@router.get("/health/live")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
def readiness(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception as error:
        logger.error(
            "Database readiness check failed",
            extra={"event": "readiness_failed", "exception_type": type(error).__name__},
        )
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "dependencies": {"database": "unavailable"},
            },
        )
    return {
        "status": "ready",
        "dependencies": {"database": "available"},
    }


def _ai_usage_summary(db: Session, since: datetime) -> dict[str, Any]:
    row = db.execute(
        select(
            func.count(AIAuditLog.id),
            func.sum(case((AIAuditLog.status == "success", 1), else_=0)),
            func.sum(
                case(
                    (
                        AIAuditLog.status.in_(("error", "validation_failed")),
                        1,
                    ),
                    else_=0,
                )
            ),
            func.sum(case((AIAuditLog.status == "started", 1), else_=0)),
            func.avg(AIAuditLog.duration_ms),
            func.sum(AIAuditLog.input_tokens),
            func.sum(AIAuditLog.output_tokens),
            func.sum(AIAuditLog.estimated_cost_usd),
            func.count(AIAuditLog.estimated_cost_usd),
        ).where(AIAuditLog.created_at >= since)
    ).one()
    by_analysis = db.execute(
        select(
            AIAuditLog.analysis_type,
            AIAuditLog.model_name,
            func.count(AIAuditLog.id),
            func.sum(AIAuditLog.input_tokens),
            func.sum(AIAuditLog.output_tokens),
            func.sum(AIAuditLog.estimated_cost_usd),
        )
        .where(AIAuditLog.created_at >= since)
        .group_by(AIAuditLog.analysis_type, AIAuditLog.model_name)
        .order_by(AIAuditLog.analysis_type, AIAuditLog.model_name)
    ).all()
    return {
        "window_hours": 24,
        "calls": int(row[0] or 0),
        "succeeded": int(row[1] or 0),
        "failed_or_invalid": int(row[2] or 0),
        "still_running": int(row[3] or 0),
        "average_duration_ms": (
            round(float(row[4]), 2) if row[4] is not None else None
        ),
        "input_tokens": int(row[5] or 0),
        "output_tokens": int(row[6] or 0),
        "estimated_cost_usd": (
            float(row[7]) if row[7] is not None else None
        ),
        "priced_calls": int(row[8] or 0),
        "by_analysis": [
            {
                "analysis_type": item.analysis_type,
                "model": item.model_name,
                "calls": int(item[2] or 0),
                "input_tokens": int(item[3] or 0),
                "output_tokens": int(item[4] or 0),
                "estimated_cost_usd": (
                    float(item[5]) if item[5] is not None else None
                ),
            }
            for item in by_analysis
        ],
        "cost_configuration": (
            "configured"
            if settings.GEMINI_INPUT_COST_PER_MILLION_TOKENS_USD is not None
            and settings.GEMINI_OUTPUT_COST_PER_MILLION_TOKENS_USD is not None
            else "not_configured"
        ),
    }


@router.get("/api/ops/health", dependencies=[Depends(require_roles("admin"))])
def operational_health(db: Session = Depends(get_db)) -> dict[str, Any]:
    database_started = time.perf_counter()
    db.execute(text("SELECT 1"))
    database_latency_ms = round(
        (time.perf_counter() - database_started) * 1000,
        2,
    )
    pool = engine.pool
    pool_metrics = {
        metric_name: metric()
        for metric_name, metric in (
            ("size", getattr(pool, "size", None)),
            ("checked_in", getattr(pool, "checkedin", None)),
            ("checked_out", getattr(pool, "checkedout", None)),
            ("overflow", getattr(pool, "overflow", None)),
        )
        if callable(metric)
    }

    job_rows = db.execute(
        select(HardwareAnalysisJob.status, func.count())
        .group_by(HardwareAnalysisJob.status)
    ).all()
    queued_oldest = db.scalar(
        select(func.min(HardwareAnalysisJob.created_at)).where(
            HardwareAnalysisJob.status == "queued"
        )
    )
    expired_leases = db.scalar(
        select(func.count())
        .select_from(HardwareAnalysisJob)
        .where(
            HardwareAnalysisJob.status == "running",
            HardwareAnalysisJob.lease_expires_at < datetime.now(timezone.utc),
        )
    ) or 0
    failed_jobs_last_24h = db.scalar(
        select(func.count())
        .select_from(HardwareAnalysisJob)
        .where(
            HardwareAnalysisJob.status == "failed",
            HardwareAnalysisJob.completed_at
            >= datetime.now(timezone.utc) - timedelta(hours=24),
        )
    ) or 0

    sync_rows = db.execute(
        select(IntegrationSyncRun.status, func.count())
        .where(
            IntegrationSyncRun.created_at
            >= datetime.now(timezone.utc) - timedelta(hours=24)
        )
        .group_by(IntegrationSyncRun.status)
    ).all()
    latest_sync = db.execute(
        select(
            IntegrationSyncRun.status,
            IntegrationSyncRun.created_at,
            IntegrationSyncRun.error_code,
        )
        .order_by(IntegrationSyncRun.created_at.desc())
        .limit(1)
    ).first()

    worker_status = get_hardware_analysis_worker_status()
    scheduler_status = get_monitoring_scheduler_status()
    ai_summary = _ai_usage_summary(
        db,
        datetime.now(timezone.utc) - timedelta(hours=24),
    )
    health_status = "ok"
    if (
        settings.HARDWARE_ANALYSIS_JOBS_ENABLED
        and worker_status["state"] != "running"
    ):
        health_status = "degraded"
    if expired_leases:
        health_status = "degraded"
    if latest_sync is not None and latest_sync.status == "failed":
        health_status = "degraded"

    return {
        "status": health_status,
        "database": {
            "status": "available",
            "query_latency_ms": database_latency_ms,
            "connection_pool": pool_metrics,
        },
        "hardware_analysis_workers": worker_status,
        "hardware_analysis_jobs": {
            "counts_by_status": {status: count for status, count in job_rows},
            "oldest_queued_at": (
                queued_oldest.isoformat() if queued_oldest is not None else None
            ),
            "expired_running_leases": int(expired_leases),
            "failed_last_24h": int(failed_jobs_last_24h),
        },
        "monitoring_scheduler": scheduler_status,
        "integrations": {
            "syncs_last_24h": {status: count for status, count in sync_rows},
            "latest_sync": (
                {
                    "status": latest_sync.status,
                    "created_at": latest_sync.created_at.isoformat(),
                    "error_code": latest_sync.error_code,
                }
                if latest_sync is not None
                else None
            ),
        },
        "ai": ai_summary,
    }


@router.get("/api/ops/metrics", dependencies=[Depends(require_roles("admin"))])
def operational_metrics(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {
        "http": request_metrics.snapshot(),
        "ai": _ai_usage_summary(
            db,
            datetime.now(timezone.utc) - timedelta(hours=24),
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
