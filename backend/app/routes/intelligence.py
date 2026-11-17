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
from app.services.api_rate_limit import rate_limit_dependency
from app.services.intelligence_orchestrator import (
    orchestrate_manufacturing_analysis,
)
from app.services.monitoring_auth import get_current_user, require_plant_access
from app.services.monitoring_auth import require_roles


router = APIRouter(
    prefix="/api/intelligence",
    tags=["Manufacturing Intelligence"],
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
                "manufacturing-intelligence",
                limit=10,
                window_seconds=60,
            )
        )
    ],
)
async def analyze_manufacturing_file(
    file: UploadFile = File(...),
    analysis_question: str = Form(default="", max_length=1000),
    column_mapping: str | None = Form(default=None, max_length=20_000),
    source_timezone: str = Form(default="UTC", max_length=80),
    downtime_unit: str = Form(default="minutes"),
    production_unit: str = Form(default="unit", max_length=40),
    include_ai: bool = Form(default=True),
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
        mapping = _parse_column_mapping(column_mapping)
        result = await run_in_threadpool(
            orchestrate_manufacturing_analysis,
            dataframe,
            source_filename=file.filename,
            question=analysis_question,
            column_mapping=mapping,
            source_timezone=source_timezone,
            downtime_unit=downtime_unit,
            production_unit=production_unit,
            include_ai=include_ai,
        )
    except HTTPException:
        raise
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    file.file.seek(0)
    digest = hashlib.sha256()
    while chunk := file.file.read(1024 * 1024):
        digest.update(chunk)
    summary = result["executive_summary"]
    history: AnalysisHistory = create_analysis_history(
        db,
        analysis_type="manufacturing_intelligence",
        title="Manufacturing Intelligence",
        summary=summary,
        source_filename=file.filename,
        source_file_sha256=digest.hexdigest(),
        algorithm_version="manufacturing-intelligence-v2",
        ai_status=result["ai_status"],
        evidence=result["evidence_references"],
        result_data=result,
        creator_user_id=user.id,
        plant_id=plant_id,
    )
    result["analysis_history_id"] = history.id
    return result
