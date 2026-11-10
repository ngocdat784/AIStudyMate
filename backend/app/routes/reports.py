import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.monitoring import (
    AlertUser,
    Department,
    Factory,
    GovernedReport,
    GovernedReportEdit,
    GovernedReportVersion,
    Organization,
    Plant,
)
from app.models.analysis_history import AnalysisHistory
from app.services.monitoring_auth import (
    accessible_plant_ids,
    get_current_user,
    has_plant_access,
    require_roles,
)
from app.services.user_audit import record_user_action
from app.services.hardware_report_service import build_hardware_pdf, build_hardware_xlsx


router = APIRouter(prefix="/api/reports", tags=["Governed Reports"])
logger = logging.getLogger(__name__)


class ReportSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_history_id: int = Field(gt=0)


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=240)
    organization_id: int = Field(gt=0)
    factory_id: int | None = Field(default=None, gt=0)
    department_id: int | None = Field(default=None, gt=0)
    plant_id: int | None = Field(default=None, gt=0)
    content: dict[str, Any]
    source_data: list[ReportSource] = Field(min_length=1, max_length=1000)


class ReportVersionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=240)
    content: dict[str, Any]
    source_data: list[ReportSource] = Field(min_length=1, max_length=1000)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    reason: str | None = Field(default=None, max_length=2000)


def _contains_report_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_contains_report_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_report_value(item) for item in value)
    if isinstance(value, float):
        return math.isfinite(value)
    return isinstance(value, (int, bool))


def _report_content_is_valid(content: dict[str, Any]) -> bool:
    body = {key: value for key, value in content.items() if key != "title"}
    if not _contains_report_value(body):
        return False
    try:
        json.dumps(content, allow_nan=False)
    except (TypeError, ValueError):
        return False
    return True


def _report_ai_status(source_data: list[dict[str, Any]]) -> str:
    source_statuses = [source.get("ai_status") for source in source_data]
    if any(
        status in {"unavailable", "temporarily_unavailable"}
        for status in source_statuses
    ):
        return "unavailable"
    if any(
        status is not None and status not in {"not_used", "not_requested"}
        for status in source_statuses
    ):
        return "available"
    return "not_used"


def _record_denied_access(
    db: Session,
    user: AlertUser,
    *,
    object_type: str,
    object_id: str,
    detail: str,
) -> None:
    record_user_action(
        db,
        actor=user,
        action="access.denied",
        object_type=object_type,
        object_id=object_id,
        outcome="denied",
        detail=detail,
    )
    db.commit()


def _report_scope(
    db: Session,
    request: ReportCreate,
    user: AlertUser,
) -> tuple[int, int | None, int | None, int | None]:
    organization = db.get(Organization, request.organization_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization không tồn tại.")

    factory_id = request.factory_id
    department_id = request.department_id
    plant_id = request.plant_id
    if plant_id is not None:
        plant = db.get(Plant, plant_id)
        if plant is None or plant.factory_id is None:
            raise HTTPException(status_code=422, detail="Plant chưa được ánh xạ tổ chức.")
        factory = db.get(Factory, plant.factory_id)
        if factory is None or factory.organization_id != organization.id:
            raise HTTPException(status_code=422, detail="Plant không thuộc Organization đã chọn.")
        if factory_id is not None and factory_id != plant.factory_id:
            raise HTTPException(status_code=422, detail="Factory không khớp với Plant.")
        if department_id is not None and department_id != plant.department_id:
            raise HTTPException(status_code=422, detail="Department không khớp với Plant.")
        factory_id = plant.factory_id
        department_id = plant.department_id
        if user.role != "admin" and not has_plant_access(db, user, plant.id):
            _record_denied_access(
                db,
                user,
                object_type="plant",
                object_id=str(plant.id),
                detail="report_scope_not_permitted",
            )
            raise HTTPException(status_code=403, detail="Không có quyền trên Plant này.")
    else:
        if user.role != "admin":
            raise HTTPException(
                status_code=403,
                detail="Operator cần chọn Plant được phân công cho báo cáo.",
            )
        if factory_id is not None:
            factory = db.get(Factory, factory_id)
            if factory is None or factory.organization_id != organization.id:
                raise HTTPException(status_code=422, detail="Factory không thuộc Organization.")
        if department_id is not None:
            department = db.get(Department, department_id)
            if department is None or department.factory_id != factory_id:
                raise HTTPException(status_code=422, detail="Department không thuộc Factory.")

    return organization.id, factory_id, department_id, plant_id


def _require_report_access(
    db: Session,
    report: GovernedReport,
    user: AlertUser,
) -> None:
    if user.role == "admin":
        return
    if report.plant_id is None or not has_plant_access(db, user, report.plant_id):
        _record_denied_access(
            db,
            user,
            object_type="governed_report",
            object_id=str(report.id),
            detail="report_scope_not_permitted",
        )
        raise HTTPException(status_code=404, detail="Không tìm thấy báo cáo.")


def _resolve_sources(
    db: Session,
    sources: list[ReportSource],
    user: AlertUser,
    report_plant_id: int | None,
) -> list[dict[str, Any]]:
    source_ids = [source.analysis_history_id for source in sources]
    if len(set(source_ids)) != len(source_ids):
        raise HTTPException(status_code=422, detail="Nguồn Analysis History bị trùng.")
    histories = db.scalars(
        select(AnalysisHistory).where(AnalysisHistory.id.in_(source_ids))
    ).all()
    by_id = {history.id: history for history in histories}
    if len(by_id) != len(source_ids):
        raise HTTPException(status_code=422, detail="Nguồn Analysis History không tồn tại.")

    result = []
    for source_id in source_ids:
        history = by_id[source_id]
        if not _contains_report_value(history.result_data):
            raise HTTPException(
                status_code=422,
                detail="Nguồn Analysis History không có dữ liệu phân tích hợp lệ.",
            )
        if user.role != "admin" and history.creator_user_id != user.id:
            _record_denied_access(
                db,
                user,
                object_type="analysis_history",
                object_id=str(history.id),
                detail="report_source_not_owned",
            )
            raise HTTPException(status_code=403, detail="Không có quyền dùng nguồn phân tích này.")
        if history.plant_id != report_plant_id:
            raise HTTPException(status_code=422, detail="Nguồn phân tích khác phạm vi Plant của báo cáo.")
        result.append(
            {
                "analysis_history_id": history.id,
                "analysis_type": history.analysis_type,
                "source_filename": history.source_filename,
                "dataset_id": history.dataset_id,
                "dataset_version": history.dataset_version,
                "source_file_sha256": history.source_file_sha256,
                "algorithm_version": history.algorithm_version,
                "ai_status": history.ai_status,
                "created_at": (
                    history.created_at.isoformat()
                    if history.created_at is not None
                    else None
                ),
            }
        )
    return result


def _version_json(version: GovernedReportVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "version": version.version,
        "content": version.content,
        "source_data": version.source_data or [],
        "validation_status": version.validation_status,
        "ai_status": version.ai_status,
        "approval_status": version.approval_status,
        "publication_status": version.publication_status,
        "created_by": version.created_by,
        "approver_user_id": version.approver_user_id,
        "rejection_reason": version.rejection_reason,
        "created_at": version.created_at,
        "submitted_at": version.submitted_at,
        "approved_at": version.approved_at,
        "published_at": version.published_at,
    }


def _report_json(
    report: GovernedReport,
    version: GovernedReportVersion,
) -> dict[str, Any]:
    return {
        "id": report.id,
        "title": report.title,
        "organization_id": report.organization_id,
        "factory_id": report.factory_id,
        "department_id": report.department_id,
        "plant_id": report.plant_id,
        "creator_user_id": report.creator_user_id,
        "current_version": report.current_version,
        "created_at": report.created_at,
        "version": _version_json(version),
    }


def _get_report_and_version(
    db: Session,
    report_id: int,
    version_number: int | None = None,
    *,
    lock: bool = False,
) -> tuple[GovernedReport, GovernedReportVersion]:
    report_query = select(GovernedReport).where(GovernedReport.id == report_id)
    if lock:
        report_query = report_query.with_for_update()
    report = db.scalar(report_query)
    if report is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy báo cáo.")
    selected_version = version_number or report.current_version
    version = db.scalar(
        select(GovernedReportVersion).where(
            GovernedReportVersion.report_id == report.id,
            GovernedReportVersion.version == selected_version,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên bản báo cáo.")
    return report, version


@router.get("")
def list_reports(
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    query = select(GovernedReport).order_by(GovernedReport.created_at.desc())
    if user.role != "admin":
        plant_ids = accessible_plant_ids(db, user) or []
        query = query.where(GovernedReport.plant_id.in_(plant_ids))

    result = []
    for report in db.scalars(query).all():
        version = db.scalar(
            select(GovernedReportVersion).where(
                GovernedReportVersion.report_id == report.id,
                GovernedReportVersion.version == report.current_version,
            )
        )
        if version is not None:
            result.append(_report_json(report, version))
    return result


@router.post("", status_code=status.HTTP_201_CREATED)
def create_report(
    request: ReportCreate,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin", "operator")),
) -> dict[str, Any]:
    organization_id, factory_id, department_id, plant_id = _report_scope(
        db, request, user
    )
    source_data = _resolve_sources(db, request.source_data, user, plant_id)
    report = GovernedReport(
        organization_id=organization_id,
        factory_id=factory_id,
        department_id=department_id,
        plant_id=plant_id,
        creator_user_id=user.id,
        title=request.title.strip(),
        current_version=1,
    )
    db.add(report)
    db.flush()
    version = GovernedReportVersion(
        report_id=report.id,
        version=1,
        content=request.content,
        source_data=source_data,
        validation_status="not_validated",
        ai_status=_report_ai_status(source_data),
        approval_status="draft",
        publication_status="unpublished",
        created_by=user.id,
    )
    db.add(version)
    db.flush()
    db.add(
        GovernedReportEdit(
            report_version_id=version.id,
            actor_user_id=user.id,
            before_data=None,
            after_data={"title": report.title, "content": request.content},
        )
    )
    record_user_action(
        db,
        actor=user,
        action="report.created",
        object_type="governed_report",
        object_id=str(report.id),
        after_data={"version": 1, "title": report.title},
    )
    db.commit()
    db.refresh(report)
    db.refresh(version)
    return _report_json(report, version)


@router.get("/{report_id}")
def get_report(
    report_id: int,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
) -> dict[str, Any]:
    report, version = _get_report_and_version(db, report_id)
    _require_report_access(db, report, user)
    result = _report_json(report, version)
    result["versions"] = [
        _version_json(item)
        for item in db.scalars(
            select(GovernedReportVersion)
            .where(GovernedReportVersion.report_id == report.id)
            .order_by(GovernedReportVersion.version.desc())
        ).all()
    ]
    return result


@router.post("/{report_id}/versions")
def create_report_version(
    report_id: int,
    request: ReportVersionUpdate,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin", "operator")),
) -> dict[str, Any]:
    report, previous = _get_report_and_version(db, report_id, lock=True)
    _require_report_access(db, report, user)
    if report.creator_user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Chỉ người tạo hoặc Admin được sửa báo cáo.")
    if previous.approval_status == "pending_approval":
        raise HTTPException(
            status_code=409,
            detail="Không thể tạo phiên bản mới khi phiên bản hiện tại đang chờ duyệt.",
        )
    source_data = _resolve_sources(
        db,
        request.source_data,
        user,
        report.plant_id,
    )

    previous_title = report.title
    version_number = report.current_version + 1
    version = GovernedReportVersion(
        report_id=report.id,
        version=version_number,
        content=request.content,
        source_data=source_data,
        validation_status="not_validated",
        ai_status=_report_ai_status(source_data),
        approval_status="draft",
        publication_status="unpublished",
        created_by=user.id,
    )
    db.add(version)
    db.flush()
    report.current_version = version_number
    report.title = request.title.strip()
    db.add(
        GovernedReportEdit(
            report_version_id=version.id,
            actor_user_id=user.id,
            before_data={
                "version": previous.version,
                "title": previous_title,
                "content": previous.content,
            },
            after_data={"title": report.title, "content": request.content},
        )
    )
    record_user_action(
        db,
        actor=user,
        action="report.version_created",
        object_type="governed_report",
        object_id=str(report.id),
        before_data={"version": previous.version},
        after_data={"version": version_number, "title": report.title},
    )
    db.commit()
    db.refresh(report)
    db.refresh(version)
    return _report_json(report, version)


@router.post("/{report_id}/submit")
def submit_report(
    report_id: int,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin", "operator")),
) -> dict[str, Any]:
    report, version = _get_report_and_version(db, report_id, lock=True)
    _require_report_access(db, report, user)
    if report.creator_user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Chỉ người tạo hoặc Admin được gửi duyệt.")
    if version.approval_status not in {"draft", "rejected"}:
        raise HTTPException(status_code=409, detail="Phiên bản báo cáo không thể gửi duyệt.")
    if not version.source_data or not _report_content_is_valid(version.content):
        raise HTTPException(status_code=422, detail="Báo cáo cần nội dung và nguồn dữ liệu hợp lệ.")
    version.validation_status = "valid"
    version.approval_status = "pending_approval"
    version.publication_status = "unpublished"
    version.submitted_at = datetime.now(timezone.utc)
    version.approver_user_id = None
    version.rejection_reason = None
    record_user_action(
        db,
        actor=user,
        action="report.submitted",
        object_type="governed_report_version",
        object_id=str(version.id),
        after_data={
            "approval_status": version.approval_status,
            "validation_status": version.validation_status,
        },
    )
    db.commit()
    return _report_json(report, version)


@router.post("/{report_id}/review")
def review_report(
    report_id: int,
    request: ReviewRequest,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin")),
) -> dict[str, Any]:
    report, version = _get_report_and_version(db, report_id, lock=True)
    if version.approval_status != "pending_approval":
        raise HTTPException(status_code=409, detail="Báo cáo không ở trạng thái chờ duyệt.")
    if request.decision == "rejected" and not (request.reason or "").strip():
        raise HTTPException(status_code=422, detail="Cần nêu lý do từ chối.")
    version.approval_status = request.decision
    version.approver_user_id = user.id
    version.rejection_reason = (
        request.reason.strip() if request.decision == "rejected" and request.reason else None
    )
    version.approved_at = datetime.now(timezone.utc) if request.decision == "approved" else None
    record_user_action(
        db,
        actor=user,
        action=f"report.{request.decision}",
        object_type="governed_report_version",
        object_id=str(version.id),
        after_data={
            "approval_status": version.approval_status,
            "reason": version.rejection_reason,
        },
    )
    db.commit()
    return _report_json(report, version)


@router.post("/{report_id}/publish")
def publish_report(
    report_id: int,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin")),
) -> dict[str, Any]:
    report, version = _get_report_and_version(db, report_id, lock=True)
    if version.approval_status != "approved":
        raise HTTPException(status_code=409, detail="Chỉ báo cáo đã duyệt mới được phát hành.")
    version.publication_status = "published"
    version.published_at = datetime.now(timezone.utc)
    record_user_action(
        db,
        actor=user,
        action="report.published",
        object_type="governed_report_version",
        object_id=str(version.id),
        after_data={"publication_status": version.publication_status},
    )
    db.commit()
    return _report_json(report, version)


@router.get("/{report_id}/export")
def export_published_report(
    report_id: int,
    file_format: Literal["pdf", "xlsx"] = Query(default="pdf", alias="format"),
    version_number: int | None = Query(default=None, ge=1, alias="version"),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
) -> StreamingResponse:
    report, version = _get_report_and_version(db, report_id, version_number)
    _require_report_access(db, report, user)
    if version.publication_status != "published":
        raise HTTPException(
            status_code=409,
            detail="Chỉ phiên bản đã phát hành mới được xuất.",
        )
    try:
        content = (
            build_hardware_pdf(version.content)
            if file_format == "pdf"
            else build_hardware_xlsx(version.content)
        )
    except Exception as exc:
        logger.exception("Could not export published report %s v%s", report_id, version.version)
        raise HTTPException(
            status_code=422,
            detail="Nội dung phiên bản không thể xuất thành định dạng yêu cầu.",
        ) from exc
    record_user_action(
        db,
        actor=user,
        action="report.exported",
        object_type="governed_report_version",
        object_id=str(version.id),
        after_data={
            "report_id": report.id,
            "version": version.version,
            "format": file_format,
        },
    )
    db.commit()
    media_type = (
        "application/pdf"
        if file_format == "pdf"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    filename = f"report-{report.id}-v{version.version}.{file_format}"
    return StreamingResponse(
        iter([content]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
