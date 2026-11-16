from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database.connection import get_db
from app.models import (
    IntegrationConnector,
    IntegrationSyncRun,
    MonitoringDataset,
)
from app.models.monitoring import AlertUser, Plant, ProductionLine
from app.routes.hardware import (
    MAX_FILE_SIZE,
    _json_safe_cell,
    _parse_hardware_content,
    _read_upload_content,
)
from app.services.integration_service import (
    CANONICAL_FIELDS,
    IntegrationValidationError,
    map_and_validate_dataframe,
    suggest_column_mapping,
    validate_connector_mapping,
)
from app.services.monitoring_auth import (
    accessible_plant_ids,
    get_current_user,
    require_plant_access,
    require_roles,
)
from app.services.api_rate_limit import rate_limit_dependency
from app.services.monitoring_service import evaluate_monitoring_dataset
from app.services.user_audit import record_user_action


router = APIRouter(
    prefix="/api/integrations",
    tags=["Industrial Data Integrations"],
)
logger = logging.getLogger(__name__)
MAX_SYNC_HISTORY_PAGE_SIZE = 100


class ConnectorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    connector_type: Literal["file"] = "file"
    plant_id: int = Field(gt=0)
    line_id: int | None = Field(default=None, gt=0)
    column_mapping: dict[str, str] = Field(min_length=2, max_length=100)
    normalization: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Tên connector không được để trống.")
        return normalized


class ConnectorUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    line_id: int | None = Field(default=None, gt=0)
    column_mapping: dict[str, str] | None = Field(
        default=None,
        min_length=2,
        max_length=100,
    )
    normalization: dict[str, Any] | None = None
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Tên connector không được để trống.")
        return normalized


def _validate_scope(
    db: Session,
    user: AlertUser,
    plant_id: int,
    line_id: int | None,
) -> None:
    if db.get(Plant, plant_id) is None:
        raise HTTPException(status_code=404, detail="Nhà máy không tồn tại.")
    if line_id is not None:
        line = db.get(ProductionLine, line_id)
        if line is None or line.plant_id != plant_id:
            raise HTTPException(
                status_code=422,
                detail="Dây chuyền không thuộc nhà máy đã chọn.",
            )
    require_plant_access(db, user, plant_id)


def _connector_json(connector: IntegrationConnector) -> dict[str, Any]:
    return {
        "id": connector.id,
        "name": connector.name,
        "connector_type": connector.connector_type,
        "plant_id": connector.plant_id,
        "line_id": connector.line_id,
        "column_mapping": connector.column_mapping,
        "normalization": connector.normalization,
        "enabled": connector.enabled,
        "last_sync_at": (
            connector.last_sync_at.isoformat()
            if connector.last_sync_at
            else None
        ),
        "created_at": connector.created_at.isoformat(),
        "updated_at": connector.updated_at.isoformat(),
    }


def _safe_filename(filename: str | None) -> str:
    return (filename or "").replace("\\", "/").rsplit("/", 1)[-1]


def _idempotency_key(
    file_sha256: str,
    mapping: dict[str, str],
    normalization: dict[str, Any],
    *,
    plant_id: int,
    line_id: int | None,
    granularity: str,
    schedule_interval: int,
    schedule_unit: str,
) -> str:
    config = {
        "file_sha256": file_sha256,
        "column_mapping": mapping,
        "normalization": normalization,
        "plant_id": plant_id,
        "line_id": line_id,
        "granularity": granularity,
        "schedule_interval": schedule_interval,
        "schedule_unit": schedule_unit,
    }
    canonical_config = json.dumps(
        config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_config.encode("utf-8")).hexdigest()


def _sync_json(sync_run: IntegrationSyncRun) -> dict[str, Any]:
    return {
        "id": sync_run.id,
        "connector_id": sync_run.connector_id,
        "status": sync_run.status,
        "source_filename": sync_run.source_filename,
        "source_file_sha256": sync_run.source_file_sha256,
        "rows_read": sync_run.rows_read,
        "rows_imported": sync_run.rows_imported,
        "rows_rejected": sync_run.rows_rejected,
        "validation_report": sync_run.validation_report,
        "lineage": sync_run.lineage,
        "dataset_id": sync_run.dataset_id,
        "error_code": sync_run.error_code,
        "error_message": sync_run.error_message,
        "created_at": sync_run.created_at.isoformat(),
        "completed_at": (
            sync_run.completed_at.isoformat()
            if sync_run.completed_at
            else None
        ),
    }


def _get_accessible_connector(
    db: Session,
    user: AlertUser,
    connector_id: str,
    *,
    for_update: bool = False,
) -> IntegrationConnector:
    statement = select(IntegrationConnector).where(
        IntegrationConnector.id == connector_id
    )
    if for_update:
        statement = statement.with_for_update()
    connector = db.scalar(statement)
    if connector is None:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy connector.",
        )
    require_plant_access(db, user, connector.plant_id)
    return connector


def _persist_failed_sync(
    db: Session,
    *,
    sync_id: str,
    connector_id: str,
    owner_user_id: int,
    filename: str,
    file_sha256: str,
    idempotency_key: str,
    lineage: dict[str, Any],
    error_code: str,
    error_message: str,
    report: dict[str, Any] | None = None,
) -> None:
    db.rollback()
    sync_run = IntegrationSyncRun(
        id=sync_id,
        connector_id=connector_id,
        owner_user_id=owner_user_id,
        status="failed",
        source_filename=filename,
        source_file_sha256=file_sha256,
        idempotency_key=idempotency_key,
        rows_read=(report or {}).get("rows_read", 0),
        rows_imported=0,
        rows_rejected=(report or {}).get("rows_rejected", 0),
        validation_report=report,
        lineage=lineage,
        error_code=error_code[:80],
        error_message=error_message[:500],
        completed_at=datetime.now(timezone.utc),
    )
    db.add(sync_run)
    db.commit()


@router.get("/registry")
def get_connector_registry(
    _: AlertUser = Depends(get_current_user),
) -> dict[str, Any]:
    return {
        "connectors": [
            {
                "type": "file",
                "name": "CSV / XLSX",
                "status": "available",
                "formats": [".csv", ".xlsx"],
                "description": (
                    "Nhập file theo lược đồ dữ liệu sản xuất HardwareAI; "
                    "hỗ trợ ánh xạ, kiểm tra và chuẩn hóa dữ liệu."
                ),
            }
        ],
        "not_connected": ["ERP", "MES", "SCADA", "PLC", "Machine API"],
        "notice": (
            "Chưa cấu hình connector hệ thống công nghiệp. "
            "Không có kết nối ERP/MES/SCADA/PLC nào được giả lập."
        ),
        "canonical_fields": list(CANONICAL_FIELDS),
        "required_fields": ["Date", "Production"],
        "max_file_size_bytes": MAX_FILE_SIZE,
        "max_rows": 100_000,
    }


@router.post(
    "/preview",
    dependencies=[
        Depends(
            rate_limit_dependency(
                "integration-preview",
                limit=10,
                window_seconds=60,
            )
        )
    ],
)
async def preview_integration_file(
    file: UploadFile = File(...),
    _: AlertUser = Depends(require_roles("admin", "operator")),
) -> dict[str, Any]:
    filename = _safe_filename(file.filename)
    if Path(filename).suffix.lower() not in {".csv", ".xlsx"}:
        raise HTTPException(
            status_code=400,
            detail="Connector file hỗ trợ CSV hoặc XLSX.",
        )
    content = await _read_upload_content(file)
    try:
        dataframe = await run_in_threadpool(
            _parse_hardware_content,
            filename,
            content,
        )
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=422,
            detail="Không thể đọc file. Kiểm tra định dạng và tiêu đề cột.",
        ) from error

    columns = [str(column) for column in dataframe.columns]
    samples = [
        {
            column: _json_safe_cell(value)
            for column, value in row.items()
        }
        for row in dataframe.head(5).to_dict(orient="records")
    ]
    return {
        "filename": filename,
        "file_sha256": hashlib.sha256(content).hexdigest(),
        "file_size_bytes": len(content),
        "row_count": len(dataframe.index),
        "columns": columns,
        "suggested_mapping": suggest_column_mapping(columns),
        "sample_rows": samples,
    }


@router.get("/connectors")
def list_integration_connectors(
    plant_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    statement = select(IntegrationConnector)
    plant_ids = accessible_plant_ids(db, user)
    if plant_ids is not None:
        statement = statement.where(
            IntegrationConnector.plant_id.in_(plant_ids)
            if plant_ids
            else False
        )
    if plant_id is not None:
        require_plant_access(db, user, plant_id)
        statement = statement.where(IntegrationConnector.plant_id == plant_id)
    connectors = db.scalars(
        statement.order_by(
            IntegrationConnector.created_at.desc(),
            IntegrationConnector.id,
        )
    ).all()
    return [_connector_json(connector) for connector in connectors]


@router.post(
    "/connectors",
    status_code=status.HTTP_201_CREATED,
)
def create_integration_connector(
    request: ConnectorCreate,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin", "operator")),
) -> dict[str, Any]:
    _validate_scope(db, user, request.plant_id, request.line_id)
    try:
        normalization = validate_connector_mapping(
            request.column_mapping,
            request.normalization,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    connector = IntegrationConnector(
        id=str(uuid4()),
        name=request.name.strip(),
        connector_type=request.connector_type,
        plant_id=request.plant_id,
        line_id=request.line_id,
        owner_user_id=user.id,
        column_mapping=request.column_mapping,
        normalization=normalization,
        enabled=True,
    )
    db.add(connector)
    record_user_action(
        db,
        actor=user,
        action="integration_connector.created",
        object_type="integration_connector",
        object_id=connector.id,
        after_data={
            "connector_type": connector.connector_type,
            "plant_id": connector.plant_id,
            "line_id": connector.line_id,
            "mapped_fields": sorted(set(request.column_mapping.values())),
        },
    )
    db.commit()
    db.refresh(connector)
    return _connector_json(connector)


@router.patch("/connectors/{connector_id}")
def update_integration_connector(
    connector_id: str,
    request: ConnectorUpdate,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin", "operator")),
) -> dict[str, Any]:
    connector = _get_accessible_connector(db, user, connector_id)
    update = request.model_dump(exclude_unset=True)
    mapping = update.get("column_mapping", connector.column_mapping)
    normalization_value = update.get(
        "normalization",
        connector.normalization,
    )
    if normalization_value is None:
        raise HTTPException(
            status_code=422,
            detail="Cấu hình chuẩn hóa không được để trống.",
        )
    if "column_mapping" in update or "normalization" in update:
        try:
            normalized = validate_connector_mapping(
                mapping,
                normalization_value,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        connector.column_mapping = mapping
        connector.normalization = normalized
    if "name" in update and update["name"] is not None:
        connector.name = update["name"].strip()
    if "enabled" in update and update["enabled"] is not None:
        connector.enabled = update["enabled"]
    if "line_id" in update:
        _validate_scope(
            db,
            user,
            connector.plant_id,
            update["line_id"],
        )
        connector.line_id = update["line_id"]

    record_user_action(
        db,
        actor=user,
        action="integration_connector.updated",
        object_type="integration_connector",
        object_id=connector.id,
        after_data={
            "enabled": connector.enabled,
            "line_id": connector.line_id,
            "mapped_fields": sorted(set(connector.column_mapping.values())),
        },
    )
    db.commit()
    db.refresh(connector)
    return _connector_json(connector)


@router.post(
    "/connectors/{connector_id}/sync",
    dependencies=[
        Depends(
            rate_limit_dependency(
                "integration-sync",
                limit=10,
                window_seconds=60,
            )
        )
    ],
)
def sync_integration_file(
    connector_id: str,
    file: UploadFile = File(...),
    granularity: Literal["day", "week", "month"] = Form(default="day"),
    schedule_interval: int = Form(default=1, ge=1, le=52),
    schedule_unit: Literal["hours", "days", "weeks"] = Form(default="days"),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin", "operator")),
) -> dict[str, Any]:
    filename = _safe_filename(file.filename)
    if Path(filename).suffix.lower() not in {".csv", ".xlsx"}:
        raise HTTPException(
            status_code=400,
            detail="Connector file hỗ trợ CSV hoặc XLSX.",
        )
    content = bytearray()
    while chunk := file.file.read(64 * 1024):
        if len(content) + len(chunk) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail="File không được vượt quá 10MB.",
            )
        content.extend(chunk)
    if not content:
        raise HTTPException(status_code=400, detail="File đang trống.")
    file_bytes = bytes(content)
    file_sha256 = hashlib.sha256(file_bytes).hexdigest()
    sync_id = str(uuid4())

    connector = _get_accessible_connector(
        db,
        user,
        connector_id,
        for_update=True,
    )
    if not connector.enabled:
        raise HTTPException(
            status_code=409,
            detail="Connector đã bị tạm dừng.",
        )
    idempotency_key = _idempotency_key(
        file_sha256,
        connector.column_mapping,
        connector.normalization,
        plant_id=connector.plant_id,
        line_id=connector.line_id,
        granularity=granularity,
        schedule_interval=schedule_interval,
        schedule_unit=schedule_unit,
    )
    existing = db.scalar(
        select(IntegrationSyncRun)
        .where(
            IntegrationSyncRun.connector_id == connector.id,
            IntegrationSyncRun.idempotency_key == idempotency_key,
            IntegrationSyncRun.status == "succeeded",
        )
        .order_by(IntegrationSyncRun.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        result = _sync_json(existing)
        result["idempotent_replay"] = True
        return result

    lineage = {
        "connector_id": connector.id,
        "connector_type": connector.connector_type,
        "source_filename": filename,
        "source_file_sha256": file_sha256,
        "mapping": connector.column_mapping,
        "normalization": connector.normalization,
        "imported_by_user_id": user.id,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    report: dict[str, Any] | None = None
    try:
        dataframe = _parse_hardware_content(filename, file_bytes)
        records, report = map_and_validate_dataframe(
            dataframe,
            mapping=connector.column_mapping,
            normalization=connector.normalization,
        )
        dataset = MonitoringDataset(
            name=f"{connector.name} — {filename}"[:200],
            plant_id=connector.plant_id,
            line_id=connector.line_id,
            uploaded_by=user.id,
            granularity=granularity,
            schedule_interval=schedule_interval,
            schedule_unit=schedule_unit,
            enabled=True,
            data_rows=records,
        )
        db.add(dataset)
        db.flush()
        evaluation = evaluate_monitoring_dataset(
            db,
            dataset,
            commit=False,
        )

        lineage = {
            **lineage,
            "sync_id": sync_id,
            "dataset_id": dataset.id,
            "rows_imported": len(records),
            "imported_at": datetime.now(timezone.utc).isoformat(),
        }
        sync_run = IntegrationSyncRun(
            id=sync_id,
            connector_id=connector.id,
            owner_user_id=user.id,
            status="succeeded",
            source_filename=filename,
            source_file_sha256=file_sha256,
            idempotency_key=idempotency_key,
            rows_read=report["rows_read"],
            rows_imported=len(records),
            rows_rejected=0,
            validation_report=report,
            lineage=lineage,
            dataset_id=dataset.id,
            completed_at=datetime.now(timezone.utc),
        )
        db.add(sync_run)
        connector.last_sync_at = sync_run.completed_at
        record_user_action(
            db,
            actor=user,
            action="integration_sync.succeeded",
            object_type="integration_sync",
            object_id=sync_id,
            after_data={
                "connector_id": connector.id,
                "dataset_id": dataset.id,
                "rows_imported": len(records),
                "source_file_sha256": file_sha256,
            },
        )
        db.commit()
        db.refresh(sync_run)
        return {
            **_sync_json(sync_run),
            "dataset": {
                "id": dataset.id,
                "row_count": len(records),
                "alerts_detected": evaluation["alerts_detected"],
            },
            "idempotent_replay": False,
        }
    except IntegrationValidationError as error:
        _persist_failed_sync(
            db,
            sync_id=sync_id,
            connector_id=connector.id,
            owner_user_id=user.id,
            filename=filename,
            file_sha256=file_sha256,
            idempotency_key=idempotency_key,
            lineage=lineage,
            error_code="validation_failed",
            error_message=str(error),
            report=error.report,
        )
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(error),
                "sync_id": sync_id,
                "validation_report": error.report,
            },
        ) from error
    except HTTPException as error:
        _persist_failed_sync(
            db,
            sync_id=sync_id,
            connector_id=connector.id,
            owner_user_id=user.id,
            filename=filename,
            file_sha256=file_sha256,
            idempotency_key=idempotency_key,
            lineage=lineage,
            error_code=f"http_{error.status_code}",
            error_message=(
                error.detail
                if isinstance(error.detail, str)
                else "Không thể đọc hoặc nhập file."
            ),
        )
        raise
    except IntegrityError as error:
        db.rollback()
        existing = db.scalar(
            select(IntegrationSyncRun)
            .where(
                IntegrationSyncRun.connector_id == connector_id,
                IntegrationSyncRun.idempotency_key == idempotency_key,
                IntegrationSyncRun.status == "succeeded",
            )
            .order_by(IntegrationSyncRun.created_at.desc())
            .limit(1)
        )
        if existing is not None:
            result = _sync_json(existing)
            result["idempotent_replay"] = True
            return result
        _persist_failed_sync(
            db,
            sync_id=sync_id,
            connector_id=connector_id,
            owner_user_id=user.id,
            filename=filename,
            file_sha256=file_sha256,
            idempotency_key=idempotency_key,
            lineage=lineage,
            error_code="persistence_conflict",
            error_message="Không thể lưu lần đồng bộ. Vui lòng thử lại.",
        )
        logger.error(
            "Integration sync persistence conflict: connector_id=%s",
            connector_id,
        )
        raise HTTPException(
            status_code=409,
            detail="Không thể lưu lần đồng bộ. Vui lòng thử lại.",
        ) from error
    except Exception as error:
        _persist_failed_sync(
            db,
            sync_id=sync_id,
            connector_id=connector.id,
            owner_user_id=user.id,
            filename=filename,
            file_sha256=file_sha256,
            idempotency_key=idempotency_key,
            lineage=lineage,
            error_code=type(error).__name__,
            error_message="Đồng bộ thất bại do lỗi nội bộ. Vui lòng thử lại.",
            report=report,
        )
        logger.error(
            "Integration sync failed: connector_id=%s error_type=%s",
            connector.id,
            type(error).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail="Đồng bộ thất bại do lỗi nội bộ. Vui lòng thử lại.",
        ) from error


@router.get("/sync-runs")
def list_integration_sync_runs(
    connector_id: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_SYNC_HISTORY_PAGE_SIZE),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
) -> dict[str, Any]:
    statement = (
        select(IntegrationSyncRun)
        .join(
            IntegrationConnector,
            IntegrationConnector.id == IntegrationSyncRun.connector_id,
        )
    )
    count_statement = (
        select(func.count())
        .select_from(IntegrationSyncRun)
        .join(
            IntegrationConnector,
            IntegrationConnector.id == IntegrationSyncRun.connector_id,
        )
    )
    plant_ids = accessible_plant_ids(db, user)
    if plant_ids is not None:
        scope_filter = IntegrationConnector.plant_id.in_(plant_ids) if plant_ids else False
        statement = statement.where(scope_filter)
        count_statement = count_statement.where(scope_filter)
    if connector_id is not None:
        statement = statement.where(
            IntegrationSyncRun.connector_id == connector_id
        )
        count_statement = count_statement.where(
            IntegrationSyncRun.connector_id == connector_id
        )
    total = db.scalar(count_statement) or 0
    items = db.scalars(
        statement
        .order_by(
            IntegrationSyncRun.created_at.desc(),
            IntegrationSyncRun.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_sync_json(sync_run) for sync_run in items],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        },
    }


@router.get("/sync-runs/{sync_id}")
def get_integration_sync_run(
    sync_id: str,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
) -> dict[str, Any]:
    sync_run = db.get(IntegrationSyncRun, sync_id)
    if sync_run is None:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy lịch sử đồng bộ.",
        )
    connector = _get_accessible_connector(
        db,
        user,
        sync_run.connector_id,
    )
    return _sync_json(sync_run)
