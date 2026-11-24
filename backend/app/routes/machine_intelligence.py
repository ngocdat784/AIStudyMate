import hashlib
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database.connection import get_db
from app.models.monitoring import AlertUser
from app.routes.hardware import _read_hardware_upload
from app.services.analysis_history_service import create_analysis_history
from app.services.api_rate_limit import rate_limit_dependency
from app.services.machine_intelligence_service import analyze_machine_intelligence
from app.services.monitoring_auth import get_current_user, require_roles


router = APIRouter(
    prefix="/api/machine-intelligence",
    tags=["Machine Intelligence"],
    dependencies=[Depends(require_roles("admin", "operator"))],
)


@router.post(
    "/analyze",
    status_code=201,
    dependencies=[
        Depends(
            rate_limit_dependency(
                "machine-intelligence",
                limit=10,
                window_seconds=60,
            )
        )
    ],
)
async def analyze_machine_file(
    file: UploadFile = File(...),
    source_timezone: str = Form(default="UTC", max_length=80),
    downtime_unit: str = Form(default="minutes"),
    duration_unit: str = Form(default="minutes"),
    cycle_time_unit: str = Form(default="seconds"),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        dataframe = await _read_hardware_upload(file)
        result = await run_in_threadpool(
            analyze_machine_intelligence,
            dataframe,
            source_timezone=source_timezone,
            downtime_unit=downtime_unit,
            duration_unit=duration_unit,
            cycle_time_unit=cycle_time_unit,
        )
    except HTTPException:
        raise
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    file.file.seek(0)
    digest = hashlib.sha256()
    while chunk := file.file.read(1024 * 1024):
        digest.update(chunk)
    create_analysis_history(
        db,
        analysis_type="machine_intelligence",
        title="Machine Intelligence",
        summary=(
            f"Phân tích {result['machine_count']} thiết bị; "
            f"{result['downtime']['event_count']} sự kiện dừng."
        ),
        source_filename=file.filename,
        source_file_sha256=digest.hexdigest(),
        algorithm_version="machine-intelligence-v1.1",
        ai_status="not_requested",
        evidence=[],
        result_data=result,
        creator_user_id=user.id,
    )
    return result
