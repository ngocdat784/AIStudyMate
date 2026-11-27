from __future__ import annotations

import logging
import hashlib
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.database.connection import SessionLocal
from app.models import HardwareAnalysisJob


logger = logging.getLogger(__name__)
_QUEUE_ADVISORY_LOCK = 0x4841524457415245
_worker_lock = threading.Lock()
_worker_stop: threading.Event | None = None
_worker_threads: list[threading.Thread] = []


def enqueue_hardware_analysis_job(
    db: Session,
    *,
    owner_user_id: int,
    filename: str,
    content: bytes,
    custom_prompt: str,
    plant_id: int | None = None,
) -> HardwareAnalysisJob:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _QUEUE_ADVISORY_LOCK},
        )

    pending = db.scalar(
        select(func.count())
        .select_from(HardwareAnalysisJob)
        .where(
            HardwareAnalysisJob.status.in_(("queued", "running")),
        )
    ) or 0
    if pending >= settings.HARDWARE_ANALYSIS_JOB_MAX_PENDING:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hàng đợi phân tích đang đầy. Vui lòng thử lại sau.",
            headers={"Retry-After": "30"},
        )

    user_pending = db.scalar(
        select(func.count())
        .select_from(HardwareAnalysisJob)
        .where(
            HardwareAnalysisJob.owner_user_id == owner_user_id,
            HardwareAnalysisJob.status.in_(("queued", "running")),
        )
    ) or 0
    if user_pending >= settings.HARDWARE_ANALYSIS_JOB_MAX_PENDING_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Bạn đã có quá nhiều tác vụ đang chờ hoặc xử lý.",
            headers={"Retry-After": "30"},
        )

    job = HardwareAnalysisJob(
        id=str(uuid4()),
        owner_user_id=owner_user_id,
        plant_id=plant_id,
        source_filename=filename[:255],
        source_file_sha256=hashlib.sha256(content).hexdigest(),
        input_file=content,
        custom_prompt=custom_prompt,
        available_at=datetime.now(timezone.utc),
    )
    db.add(job)
    try:
        db.commit()
        db.refresh(job)
    except Exception:
        db.rollback()
        raise
    return job


def serialize_hardware_analysis_job(
    job: HardwareAnalysisJob,
    *,
    include_result: bool = True,
) -> dict:
    result = {
        "id": job.id,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "source_filename": job.source_filename,
        "plant_id": job.plant_id,
        "attempt_count": job.attempt_count,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "completed_at": (
            job.completed_at.isoformat() if job.completed_at else None
        ),
    }
    if include_result and job.status == "succeeded":
        result["result_data"] = job.result_data
    return result


def update_hardware_analysis_job_stage(
    job_id: str,
    *,
    stage: str,
    progress: int,
) -> None:
    with SessionLocal() as db:
        job = db.scalar(
            select(HardwareAnalysisJob)
            .where(HardwareAnalysisJob.id == job_id)
            .with_for_update()
        )
        if job is None or job.status != "running":
            return
        job.stage = stage
        job.progress = min(max(progress, 0), 99)
        job.lease_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=settings.HARDWARE_ANALYSIS_JOB_LEASE_SECONDS
        )
        db.commit()


def fail_hardware_analysis_job(job_id: str, error: Exception) -> None:
    with SessionLocal() as db:
        job = db.scalar(
            select(HardwareAnalysisJob)
            .where(HardwareAnalysisJob.id == job_id)
            .with_for_update()
        )
        if job is None or job.status != "running":
            return

        job.error_code = type(error).__name__[:80]
        job.lease_expires_at = None
        input_error = isinstance(error, ValueError) or (
            isinstance(error, HTTPException)
            and 400 <= error.status_code < 500
        )
        if (
            not input_error
            and job.attempt_count < settings.HARDWARE_ANALYSIS_JOB_MAX_ATTEMPTS
        ):
            delay_seconds = min(30, 2 ** (job.attempt_count - 1))
            job.status = "queued"
            job.stage = "retry_wait"
            job.progress = 0
            job.error_message = (
                "Phân tích gặp lỗi tạm thời; hệ thống sẽ tự động thử lại."
            )
            job.available_at = datetime.now(timezone.utc) + timedelta(
                seconds=delay_seconds
            )
        else:
            job.status = "failed"
            job.stage = "failed"
            job.error_message = (
                (
                    str(error.detail)
                    if isinstance(error, HTTPException)
                    else str(error)
                )[:1000]
                if input_error
                else (
                    "Không thể hoàn tất phân tích sau nhiều lần thử. "
                    "Vui lòng kiểm tra file hoặc thử lại sau."
                )
            )
            job.input_file = None
            job.custom_prompt = ""
            job.completed_at = datetime.now(timezone.utc)
        db.commit()


def _claim_next_job() -> str | None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        eligible = or_(
            and_(
                HardwareAnalysisJob.status == "queued",
                HardwareAnalysisJob.available_at <= now,
            ),
            and_(
                HardwareAnalysisJob.status == "running",
                or_(
                    HardwareAnalysisJob.lease_expires_at.is_(None),
                    HardwareAnalysisJob.lease_expires_at <= now,
                ),
            ),
        )
        job = db.scalar(
            select(HardwareAnalysisJob)
            .where(eligible)
            .order_by(HardwareAnalysisJob.created_at, HardwareAnalysisJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None

        if (
            job.status == "running"
            and job.attempt_count >= settings.HARDWARE_ANALYSIS_JOB_MAX_ATTEMPTS
        ):
            job.status = "failed"
            job.stage = "failed"
            job.error_code = "worker_lease_expired"
            job.error_message = (
                "Tác vụ bị gián đoạn và đã vượt quá số lần thử cho phép."
            )
            job.input_file = None
            job.custom_prompt = ""
            job.lease_expires_at = None
            job.completed_at = now
            db.commit()
            return None

        job.status = "running"
        job.stage = "starting"
        job.progress = 1
        job.attempt_count += 1
        job.started_at = job.started_at or now
        job.error_code = None
        job.error_message = None
        job.lease_expires_at = now + timedelta(
            seconds=settings.HARDWARE_ANALYSIS_JOB_LEASE_SECONDS
        )
        db.commit()
        return job.id


def _worker_loop(
    stop_event: threading.Event,
    processor: Callable[[str], None],
) -> None:
    while not stop_event.is_set():
        try:
            job_id = _claim_next_job()
        except Exception:
            logger.exception("Could not claim a hardware analysis job")
            stop_event.wait(5)
            continue

        if job_id is None:
            stop_event.wait(settings.HARDWARE_ANALYSIS_JOB_POLL_SECONDS)
            continue

        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=_heartbeat_job_lease,
            args=(job_id, heartbeat_stop),
            name=f"hardware-analysis-lease-{job_id[:8]}",
            daemon=True,
        )
        heartbeat.start()
        try:
            processor(job_id)
        except Exception as error:
            logger.error(
                "Hardware analysis job processor failed: job_id=%s "
                "error_type=%s",
                job_id,
                type(error).__name__,
            )
            try:
                fail_hardware_analysis_job(job_id, error)
            except Exception:
                logger.error(
                    "Could not persist hardware analysis job failure: "
                    "job_id=%s error_type=%s",
                    job_id,
                    type(error).__name__,
                )
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=5)


def _heartbeat_job_lease(
    job_id: str,
    stop_event: threading.Event,
) -> None:
    interval = max(10, settings.HARDWARE_ANALYSIS_JOB_LEASE_SECONDS // 3)
    while not stop_event.wait(interval):
        try:
            with SessionLocal() as db:
                job = db.scalar(
                    select(HardwareAnalysisJob)
                    .where(
                        HardwareAnalysisJob.id == job_id,
                        HardwareAnalysisJob.status == "running",
                    )
                    .with_for_update()
                )
                if job is None:
                    return
                job.lease_expires_at = datetime.now(timezone.utc) + timedelta(
                    seconds=settings.HARDWARE_ANALYSIS_JOB_LEASE_SECONDS
                )
                db.commit()
        except Exception:
            logger.exception(
                "Could not renew hardware analysis job lease: job_id=%s",
                job_id,
            )


def start_hardware_analysis_workers(
    processor: Callable[[str], None],
) -> None:
    global _worker_stop, _worker_threads
    with _worker_lock:
        if any(thread.is_alive() for thread in _worker_threads):
            return
        _worker_stop = threading.Event()
        _worker_threads = []
        for index in range(settings.HARDWARE_ANALYSIS_JOB_WORKERS):
            thread = threading.Thread(
                target=_worker_loop,
                args=(_worker_stop, processor),
                name=f"hardware-analysis-worker-{index + 1}",
                daemon=True,
            )
            thread.start()
            _worker_threads.append(thread)
        logger.info(
            "Started %d hardware analysis workers",
            len(_worker_threads),
        )


def get_hardware_analysis_worker_status() -> dict[str, int | bool | str]:
    with _worker_lock:
        alive = sum(thread.is_alive() for thread in _worker_threads)
    if not settings.HARDWARE_ANALYSIS_JOBS_ENABLED:
        state = "disabled"
    elif alive:
        state = "running"
    else:
        state = "stopped"
    return {
        "state": state,
        "workers_alive": alive,
        "workers_configured": settings.HARDWARE_ANALYSIS_JOB_WORKERS,
    }


def stop_hardware_analysis_workers() -> None:
    global _worker_stop, _worker_threads
    with _worker_lock:
        stop_event = _worker_stop
        threads = list(_worker_threads)
        if stop_event is None:
            return
        stop_event.set()

    for thread in threads:
        thread.join(timeout=10)
        if thread.is_alive():
            logger.warning(
                "Hardware analysis worker did not stop before timeout: %s",
                thread.name,
            )

    with _worker_lock:
        if _worker_stop is not stop_event:
            return
        alive_threads = [
            thread for thread in threads
            if thread.is_alive()
        ]
        if alive_threads:
            _worker_threads = alive_threads
            return
        _worker_stop = None
        _worker_threads = []
