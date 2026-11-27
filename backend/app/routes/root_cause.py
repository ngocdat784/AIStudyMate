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
from app.services.root_cause_intelligence_service import (
    analyze_root_cause_intelligence,
)


router = APIRouter(
    prefix="/api/root-cause",
    tags=["Root Cause Intelligence"],
    dependencies=[Depends(require_roles("admin", "operator"))],
)


def _parse_mapping(value: str | None) -> dict[str, str] | None:
    if not value:
        return None
    try:
        mapping = json.loads(value)
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=422,
            detail="Column mapping must be valid JSON.",
        ) from error
    if not isinstance(mapping, dict) or any(
        not isinstance(source, str) or not isinstance(target, str)
        for source, target in mapping.items()
    ):
        raise HTTPException(
            status_code=422,
            detail="Column mapping must map string source names to string targets.",
        )
    return mapping


_DEFAULT_GROUP_DIMENSIONS = ["product", "process", "line", "shift"]


def _parse_dimensions(value: str | None) -> list[str]:
    if value is None or not value.strip():
        return list(_DEFAULT_GROUP_DIMENSIONS)
    if value.strip().casefold() == "null":
        return list(_DEFAULT_GROUP_DIMENSIONS)
    try:
        dimensions = json.loads(value)
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=422,
            detail="Group dimensions must be a JSON array.",
        ) from error
    if not isinstance(dimensions, list) or any(
        not isinstance(item, str) for item in dimensions
    ):
        raise HTTPException(
            status_code=422,
            detail="Group dimensions must be a JSON array of strings.",
        )
    return dimensions


def _hash_uploads(files: list[UploadFile]) -> str:
    digest = hashlib.sha256()
    for file in files:
        digest.update((file.filename or "").encode("utf-8"))
        digest.update(b"\0")
        file.file.seek(0)
        while chunk := file.file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@router.post(
    "/analyze",
    status_code=201,
    dependencies=[
        Depends(
            rate_limit_dependency(
                "root-cause-analysis",
                limit=10,
                window_seconds=60,
            )
        )
    ],
)
async def analyze_root_cause(
    problem_statement: str = Form(..., max_length=4000),
    file: UploadFile | None = File(default=None),
    production_file: UploadFile | None = File(default=None),
    quality_file: UploadFile | None = File(default=None),
    column_mapping: str | None = Form(default=None, max_length=20_000),
    production_column_mapping: str | None = Form(
        default=None,
        max_length=20_000,
    ),
    quality_column_mapping: str | None = Form(
        default=None,
        max_length=20_000,
    ),
    source_timezone: str = Form(default="UTC", max_length=80),
    downtime_unit: str = Form(default="minutes"),
    cycle_time_unit: str = Form(default="seconds"),
    duration_unit: str = Form(default="minutes"),
    production_unit: str = Form(default="unit", max_length=40),
    time_bucket: str = Form(default="day"),
    group_dimensions: str = Form(
        default='["product","process","line","shift"]',
        max_length=500,
    ),
    outcome_metric: str = Form(default="defect_rate"),
    include_ai: bool = Form(default=True),
    plant_id: int | None = Form(default=None, gt=0),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
) -> dict[str, Any]:
    if plant_id is not None:
        if db.get(Plant, plant_id) is None:
            raise HTTPException(status_code=404, detail="Plant not found.")
        require_plant_access(db, user, plant_id)

    split_mode = production_file is not None or quality_file is not None
    if (file is not None and split_mode) or (
        file is None and (production_file is None or quality_file is None)
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "Upload one combined file, or provide both production_file "
                "and quality_file."
            ),
        )

    uploads = (
        [production_file, quality_file]
        if split_mode
        else [file]
    )
    valid_uploads = [upload for upload in uploads if upload is not None]
    if len(valid_uploads) != len(uploads):
        raise HTTPException(status_code=422, detail="Upload is incomplete.")

    try:
        if split_mode:
            if production_file is None or quality_file is None:
                raise HTTPException(
                    status_code=422,
                    detail="Both production and quality files are required.",
                )
            production_dataframe = await _read_hardware_upload(
                production_file
            )
            quality_dataframe = await _read_hardware_upload(quality_file)
            production_classification = classify_analysis_dataset(
                production_dataframe,
                column_mapping=_parse_mapping(production_column_mapping),
            )
            quality_classification = classify_analysis_dataset(
                quality_dataframe,
                column_mapping=_parse_mapping(quality_column_mapping),
            )
            ensure_analysis_ready(production_classification, "production")
            ensure_analysis_ready(quality_classification, "quality")
            classification = {
                "mode": "split",
                "production_file": production_classification,
                "quality_file": quality_classification,
            }
            dataframe = None
        else:
            if file is None:
                raise HTTPException(
                    status_code=422,
                    detail="A combined file is required.",
                )
            dataframe = await _read_hardware_upload(file)
            classification = classify_analysis_dataset(
                dataframe,
                column_mapping=_parse_mapping(column_mapping),
            )
            ensure_analysis_ready(classification, "root_cause")
            production_dataframe = None
            quality_dataframe = None
        mapping = _parse_mapping(column_mapping)
        production_mapping = _parse_mapping(production_column_mapping)
        quality_mapping = _parse_mapping(quality_column_mapping)
        dimensions = _parse_dimensions(group_dimensions)
        result = await run_in_threadpool(
            analyze_root_cause_intelligence,
            dataframe,
            problem_statement=problem_statement,
            production_dataframe=production_dataframe,
            quality_dataframe=quality_dataframe,
            source_timezone=source_timezone,
            downtime_unit=downtime_unit,
            cycle_time_unit=cycle_time_unit,
            duration_unit=duration_unit,
            production_unit=production_unit,
            column_mapping=mapping,
            production_column_mapping=production_mapping,
            quality_column_mapping=quality_mapping,
            time_bucket=time_bucket,
            group_dimensions=dimensions,
            outcome_metric=outcome_metric,
            include_ai=include_ai,
        )
        result["dataset_classification"] = classification
    except HTTPException:
        raise
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    source_filenames = [
        upload.filename for upload in valid_uploads if upload.filename
    ]
    result["data_scope"]["source_files"] = source_filenames
    source_filename = " + ".join(source_filenames)[:255] or None
    history: AnalysisHistory = create_analysis_history(
        db,
        analysis_type="root_cause_intelligence",
        title="Root Cause Intelligence",
        summary=problem_statement.strip(),
        source_filename=source_filename,
        source_file_sha256=_hash_uploads(valid_uploads),
        algorithm_version="root-cause-intelligence-v2.3",
        ai_status=result["ai_status"],
        evidence=result["evidence_references"],
        result_data=result,
        creator_user_id=user.id,
        plant_id=plant_id,
    )
    result["analysis_history_id"] = history.id
    return result
