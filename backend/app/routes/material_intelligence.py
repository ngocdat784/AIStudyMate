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
from app.services.material_intelligence_service import analyze_material_intelligence
from app.services.monitoring_auth import get_current_user, require_roles


router = APIRouter(
    prefix="/api/material-intelligence",
    tags=["Material Intelligence"],
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
                "material-intelligence",
                limit=10,
                window_seconds=60,
            )
        )
    ],
)
async def analyze_material_file(
    file: UploadFile = File(...),
    column_mapping: str | None = Form(default=None, max_length=20_000),
    source_timezone: str = Form(default="UTC", max_length=80),
    production_unit: str = Form(default="unit", max_length=40),
    include_ai: bool = Form(default=False),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        dataframe = await _read_hardware_upload(file)
        mapping = _parse_column_mapping(column_mapping)
        result = await run_in_threadpool(
            analyze_material_intelligence,
            dataframe,
            source_timezone=source_timezone,
            production_unit=production_unit,
            column_mapping=mapping,
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
    history = create_analysis_history(
        db,
        analysis_type="material_intelligence",
        title="Material Intelligence",
        summary=result["executive_summary"],
        source_filename=file.filename,
        source_file_sha256=digest.hexdigest(),
        algorithm_version="material-intelligence-v1",
        ai_status=result["ai_status"],
        evidence=result["evidence_references"],
        result_data=result,
        creator_user_id=user.id,
    )
    result["analysis_history_id"] = history.id
    return result
