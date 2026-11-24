import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database.connection import get_db
from app.models.monitoring import AlertUser
from app.routes.hardware import _read_hardware_upload
from app.services.analysis_history_service import create_analysis_history
from app.services.api_rate_limit import rate_limit_dependency
from app.services.monitoring_auth import get_current_user, require_roles
from app.services.predictive_intelligence_service import (
    analyze_predictive_intelligence,
)


router = APIRouter(
    prefix="/api/predictive-intelligence",
    tags=["Predictive Intelligence"],
    dependencies=[Depends(require_roles("admin", "operator"))],
)


def _parse_column_mapping(value: str | None) -> dict[str, str] | None:
    if not value:
        return None
    try:
        mapping = json.loads(value)
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=422,
            detail="column_mapping phải là JSON hợp lệ.",
        ) from error
    if not isinstance(mapping, dict) or any(
        not isinstance(source, str) or not isinstance(target, str)
        for source, target in mapping.items()
    ):
        raise HTTPException(
            status_code=422,
            detail="column_mapping phải là object ánh xạ chuỗi sang chuỗi.",
        )
    return mapping


@router.post(
    "/analyze",
    status_code=201,
    dependencies=[
        Depends(
            rate_limit_dependency(
                "predictive-intelligence",
                limit=10,
                window_seconds=60,
            )
        )
    ],
)
async def analyze_predictive_file(
    file: UploadFile = File(...),
    column_mapping: str | None = Form(default=None, max_length=20_000),
    source_timezone: str = Form(default="UTC", max_length=80),
    downtime_unit: str = Form(default="minutes"),
    duration_unit: str = Form(default="minutes"),
    production_unit: str = Form(default="unit", max_length=40),
    cycle_time_unit: str = Form(default="seconds"),
    horizon: int = Form(default=7, ge=1, le=30),
    defect_threshold_percent: float = Form(default=5, ge=0, le=100),
    capacity_change_percent: float = Form(default=0, ge=-100, le=200),
    downtime_reduction_percent: float = Form(default=0, ge=0, le=100),
    material_shortage_percent: float = Form(default=0, ge=0, le=100),
    quality_improvement_percentage_points: float = Form(
        default=0,
        ge=0,
        le=100,
    ),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        dataframe = await _read_hardware_upload(file)
        mapping = _parse_column_mapping(column_mapping)
        result = await run_in_threadpool(
            analyze_predictive_intelligence,
            dataframe,
            column_mapping=mapping,
            source_timezone=source_timezone,
            downtime_unit=downtime_unit,
            duration_unit=duration_unit,
            production_unit=production_unit,
            cycle_time_unit=cycle_time_unit,
            horizon=horizon,
            defect_threshold_percent=defect_threshold_percent,
            capacity_change_percent=capacity_change_percent,
            downtime_reduction_percent=downtime_reduction_percent,
            material_shortage_percent=material_shortage_percent,
            quality_improvement_percentage_points=(
                quality_improvement_percentage_points
            ),
        )
    except HTTPException:
        raise
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    file.file.seek(0)
    digest = hashlib.sha256()
    while chunk := file.file.read(1024 * 1024):
        digest.update(chunk)
    history = create_analysis_history(
        db,
        analysis_type="predictive_intelligence",
        title="Predictive Intelligence",
        summary=(
            f"Dự báo {result['forecast_horizon_periods']} ngày từ "
            f"{result['predictive_data_foundation']['observed_period_count']} "
            "ngày dữ liệu lịch sử."
        ),
        source_filename=file.filename,
        source_file_sha256=digest.hexdigest(),
        algorithm_version="predictive-intelligence-v1",
        ai_status="not_requested",
        evidence=[],
        result_data=result,
        creator_user_id=user.id,
    )
    result["analysis_history_id"] = history.id
    return result
