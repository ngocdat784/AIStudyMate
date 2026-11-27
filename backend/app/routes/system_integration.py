from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database.connection import get_db
from app.models import AnalysisHistory
from app.models.monitoring import AlertUser, Plant
from app.routes.hardware import _read_hardware_upload
from app.services.analysis_history_service import create_analysis_history
from app.services.analysis_readiness_service import (
    classify_analysis_dataset,
    ensure_analysis_ready,
)
from app.services.api_rate_limit import rate_limit_dependency
from app.services.monitoring_auth import (
    get_current_user,
    require_plant_access,
    require_roles,
)
from app.services.system_integration_service import analyze_system_integration
from app.services.user_audit import record_user_action


router = APIRouter(
    prefix="/api/system-integration",
    tags=["System Integration"],
    dependencies=[Depends(require_roles("admin", "operator"))],
)
_MAX_INTEGRATION_ROWS = 2_000


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
                "system-integration-analysis",
                limit=3,
                window_seconds=60,
            )
        )
    ],
)
async def analyze_system_file(
    file: UploadFile = File(...),
    problem_statement: str = Form(
        default="Đánh giá liên hệ giữa các yếu tố sản xuất và chất lượng.",
        max_length=4000,
    ),
    column_mapping: str | None = Form(default=None, max_length=20_000),
    source_timezone: str = Form(default="UTC", max_length=80),
    downtime_unit: str = Form(default="minutes"),
    cycle_time_unit: str = Form(default="seconds"),
    duration_unit: str = Form(default="minutes"),
    production_unit: str = Form(default="unit", max_length=40),
    measurement_unit: str = Form(default="unit", max_length=40),
    horizon: int = Form(default=7, ge=1, le=30),
    include_ai: bool = Form(default=False),
    plant_id: int | None = Form(default=None, gt=0),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
) -> dict[str, Any]:
    if plant_id is not None:
        if db.get(Plant, plant_id) is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy nhà máy.")
        require_plant_access(db, user, plant_id)

    try:
        dataframe = await _read_hardware_upload(file)
        if len(dataframe.index) > _MAX_INTEGRATION_ROWS:
            raise HTTPException(
                status_code=413,
                detail=(
                    "System Integration xử lý tối đa 2,000 dòng mỗi yêu cầu; "
                    "hãy lọc hoặc tổng hợp dữ liệu trước khi chạy."
                ),
            )
        mapping = _parse_column_mapping(column_mapping)
        classification = classify_analysis_dataset(
            dataframe,
            column_mapping=mapping,
        )
        ensure_analysis_ready(classification, "system_integration")
        result = await run_in_threadpool(
            analyze_system_integration,
            dataframe,
            problem_statement=problem_statement,
            column_mapping=mapping,
            source_timezone=source_timezone,
            downtime_unit=downtime_unit,
            cycle_time_unit=cycle_time_unit,
            duration_unit=duration_unit,
            production_unit=production_unit,
            measurement_unit=measurement_unit,
            horizon=horizon,
            include_ai=include_ai,
        )
        result["dataset_classification"] = classification
    except HTTPException:
        raise
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    file.file.seek(0)
    digest = hashlib.sha256()
    while chunk := file.file.read(1024 * 1024):
        digest.update(chunk)
    history: AnalysisHistory = create_analysis_history(
        db,
        analysis_type="system_integration",
        title="System Integration",
        summary=(
            f"Đã tổng hợp {len(result['domains'])} Intelligence; "
            f"{len(result['cross_intelligence']['findings'])} tín hiệu liên miền."
        ),
        source_filename=file.filename,
        source_file_sha256=digest.hexdigest(),
        algorithm_version="system-integration-v1",
        ai_status=result["ai_orchestration"]["status"],
        evidence=result["evidence_references"],
        result_data=result,
        creator_user_id=user.id,
        plant_id=plant_id,
    )
    result["analysis_history_id"] = history.id
    record_user_action(
        db,
        actor=user,
        action="analysis.system_integration.completed",
        object_type="analysis_history",
        object_id=str(history.id),
        after_data={
            "analysis_type": "system_integration",
            "domain_count": len(result["domains"]),
            "cross_finding_count": len(
                result["cross_intelligence"]["findings"]
            ),
            "ai_status": result["ai_orchestration"]["status"],
        },
    )
    db.commit()
    return result
