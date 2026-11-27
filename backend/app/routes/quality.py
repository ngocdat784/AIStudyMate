from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database.connection import get_db
from app.models import AnalysisHistory
from app.models.monitoring import AlertUser
from app.routes.hardware import _read_hardware_upload
from app.services.analysis_history_service import create_analysis_history
from app.services.analysis_readiness_service import (
    classify_analysis_dataset,
    ensure_analysis_ready,
)
from app.services.api_rate_limit import rate_limit_dependency
from app.services.monitoring_auth import get_current_user, require_roles
from app.services.quality_intelligence_service import (
    analyze_quality_intelligence,
)


router = APIRouter(
    prefix="/api/quality",
    tags=["Quality Intelligence"],
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
                "quality-analysis",
                limit=10,
                window_seconds=60,
            )
        )
    ],
)
async def analyze_quality_file(
    file: UploadFile = File(...),
    column_mapping: str | None = Form(default=None, max_length=20_000),
    source_timezone: str = Form(default="UTC", max_length=80),
    downtime_unit: str = Form(default="minutes"),
    production_unit: str = Form(default="unit", max_length=40),
    measurement_unit: str = Form(default="unit", max_length=40),
    include_ai: bool = Form(default=True),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        dataframe = await _read_hardware_upload(file)
        mapping = _parse_column_mapping(column_mapping)
        classification = classify_analysis_dataset(
            dataframe,
            column_mapping=mapping,
        )
        ensure_analysis_ready(classification, "quality")
        result = await run_in_threadpool(
            analyze_quality_intelligence,
            dataframe,
            source_timezone=source_timezone,
            downtime_unit=downtime_unit,
            production_unit=production_unit,
            measurement_unit=measurement_unit,
            column_mapping=mapping,
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
        analysis_type="quality_analysis",
        title="Quality Intelligence",
        summary=result["executive_summary"],
        source_filename=file.filename,
        source_file_sha256=digest.hexdigest(),
        algorithm_version="quality-intelligence-v2.2",
        ai_status=result["ai_status"],
        evidence=result["evidence_references"],
        result_data=result,
        creator_user_id=user.id,
    )
    result["analysis_history_id"] = history.id
    return result
