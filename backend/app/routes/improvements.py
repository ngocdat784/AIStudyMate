from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models import ImprovementCase
from app.models.monitoring import Alert, AlertUser, Plant, ProductionLine
from app.services.monitoring_auth import (
    accessible_plant_ids,
    get_current_user,
    require_plant_access,
    require_roles,
)
from app.services.user_audit import record_user_action


router = APIRouter(
    prefix="/api/improvements",
    tags=["Continuous Improvement"],
)
MAX_CASE_PAGE_SIZE = 100


class KPIObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float = Field(allow_inf_nan=False)
    sample_count: int = Field(gt=0)
    standard_deviation: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )


class ImprovementCaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plant_id: int = Field(gt=0)
    line_id: int | None = Field(default=None, gt=0)
    source_alert_id: int | None = Field(default=None, gt=0)
    problem: str = Field(min_length=1, max_length=4000)
    baseline: dict[str, KPIObservation] = Field(min_length=1, max_length=50)
    owner_user_id: int | None = Field(default=None, gt=0)

    @field_validator("problem")
    @classmethod
    def normalize_problem(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Mô tả vấn đề không được để trống.")
        return normalized


class ImprovementCaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_cause: str | None = Field(default=None, max_length=4000)
    action: str | None = Field(default=None, max_length=4000)
    owner_user_id: int | None = Field(default=None, gt=0)
    implemented_at: datetime | None = None
    after_measurement: dict[str, KPIObservation] | None = Field(
        default=None,
        min_length=1,
        max_length=50,
    )
    confounders: list[str] | None = Field(default=None, max_length=50)
    conclusion: str | None = Field(default=None, max_length=4000)
    close: bool | None = None

    @field_validator("root_cause", "action", "conclusion")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("confounders")
    @classmethod
    def normalize_confounders(
        cls,
        value: list[str] | None,
    ) -> list[str] | None:
        if value is None:
            return None
        normalized = [item.strip() for item in value if item.strip()]
        if len(normalized) != len(value):
            raise ValueError("Yếu tố gây nhiễu không được để trống.")
        return normalized


def _case_status(case: ImprovementCase) -> str:
    if case.closed_at is not None:
        return "closed"
    if case.conclusion and case.after_measurement:
        return "evaluated"
    if case.after_measurement:
        return "measured"
    if case.implemented_at:
        return "implemented"
    if case.root_cause and case.action and case.owner_user_id:
        return "action_planned"
    return "detected"


def _kpi_changes(case: ImprovementCase) -> dict[str, dict[str, float | None]]:
    if not case.after_measurement:
        return {}
    changes: dict[str, dict[str, float | None]] = {}
    for name in sorted(set(case.baseline) & set(case.after_measurement)):
        before = case.baseline[name]["value"]
        after = case.after_measurement[name]["value"]
        absolute = after - before
        relative = absolute / abs(before) * 100 if before != 0 else None
        changes[name] = {
            "baseline": before,
            "after": after,
            "absolute_change": absolute,
            "relative_change_percent": relative,
        }
    return changes


def _evidence_confidence(case: ImprovementCase) -> str:
    if not case.after_measurement:
        return "not_assessed"
    common = set(case.baseline) & set(case.after_measurement)
    if not common or any(
        min(
            case.baseline[name]["sample_count"],
            case.after_measurement[name]["sample_count"],
        )
        < 30
        for name in common
    ):
        return "limited"
    if (
        case.confounders
        and all(
            case.baseline[name]["standard_deviation"] is not None
            and case.after_measurement[name]["standard_deviation"] is not None
            for name in common
        )
    ):
        return "well_documented"
    return "moderate"


def _confidence_basis() -> str:
    return (
        "Nhãn mô tả mức độ đầy đủ của dữ liệu được ghi nhận, không phải "
        "khoảng tin cậy thống kê hay bằng chứng nhân quả."
    )


def _case_json(case: ImprovementCase) -> dict[str, Any]:
    return {
        "id": case.id,
        "plant_id": case.plant_id,
        "line_id": case.line_id,
        "source_alert_id": case.source_alert_id,
        "created_by_user_id": case.created_by_user_id,
        "owner_user_id": case.owner_user_id,
        "problem": case.problem,
        "baseline": case.baseline,
        "root_cause": case.root_cause,
        "action": case.action,
        "implemented_at": (
            case.implemented_at.isoformat() if case.implemented_at else None
        ),
        "after_measurement": case.after_measurement,
        "kpi_changes": _kpi_changes(case),
        "confounders": case.confounders,
        "evidence_confidence": _evidence_confidence(case),
        "confidence_basis": _confidence_basis(),
        "conclusion": case.conclusion,
        "causality_notice": (
            "Mức thay đổi được quan sát không tự chứng minh hành động là nguyên nhân."
        ),
        "status": _case_status(case),
        "closed_at": case.closed_at.isoformat() if case.closed_at else None,
        "created_at": case.created_at.isoformat(),
        "updated_at": case.updated_at.isoformat(),
    }


def _get_case(
    db: Session,
    user: AlertUser,
    case_id: str,
) -> ImprovementCase:
    case = db.get(ImprovementCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ cải tiến.")
    require_plant_access(db, user, case.plant_id)
    return case


def _validate_case_scope(
    db: Session,
    user: AlertUser,
    payload: ImprovementCaseCreate,
) -> None:
    plant = db.get(Plant, payload.plant_id)
    if plant is None:
        raise HTTPException(status_code=404, detail="Nhà máy không tồn tại.")
    require_plant_access(db, user, payload.plant_id)
    if payload.line_id is not None:
        line = db.get(ProductionLine, payload.line_id)
        if line is None or line.plant_id != payload.plant_id:
            raise HTTPException(
                status_code=422,
                detail="Dây chuyền không thuộc nhà máy đã chọn.",
            )
    if payload.source_alert_id is not None:
        alert = db.get(Alert, payload.source_alert_id)
        if alert is None or alert.plant_id != payload.plant_id:
            raise HTTPException(
                status_code=422,
                detail="Cảnh báo không thuộc nhà máy đã chọn.",
            )
        if payload.line_id is not None and alert.line_id not in (
            None,
            payload.line_id,
        ):
            raise HTTPException(
                status_code=422,
                detail="Cảnh báo không thuộc dây chuyền đã chọn.",
            )


@router.get("")
def list_improvement_cases(
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_CASE_PAGE_SIZE),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
) -> dict[str, Any]:
    query = select(ImprovementCase)
    plant_ids = accessible_plant_ids(db, user)
    if plant_ids is not None:
        if not plant_ids:
            return {"items": [], "page": page, "page_size": page_size, "total": 0}
        query = query.where(ImprovementCase.plant_id.in_(plant_ids))
    if status_filter is not None:
        allowed_statuses = {
            "detected",
            "action_planned",
            "implemented",
            "measured",
            "evaluated",
            "closed",
        }
        if status_filter not in allowed_statuses:
            raise HTTPException(status_code=422, detail="Trạng thái không hợp lệ.")
        query = query.where(ImprovementCase.status == status_filter)
    total = db.scalar(
        select(func.count()).select_from(query.subquery())
    ) or 0
    items = db.scalars(
        query.order_by(
            ImprovementCase.created_at.desc(),
            ImprovementCase.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_case_json(item) for item in items],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@router.post("", status_code=201)
def create_improvement_case(
    payload: ImprovementCaseCreate,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin", "operator")),
) -> dict[str, Any]:
    _validate_case_scope(db, user, payload)
    owner_id = payload.owner_user_id or user.id
    owner = db.get(AlertUser, owner_id)
    if owner is None or not owner.is_active:
        raise HTTPException(status_code=422, detail="Người phụ trách không hợp lệ.")
    require_plant_access(db, owner, payload.plant_id, actor=user)
    case = ImprovementCase(
        id=str(uuid4()),
        plant_id=payload.plant_id,
        line_id=payload.line_id,
        source_alert_id=payload.source_alert_id,
        created_by_user_id=user.id,
        owner_user_id=owner_id,
        problem=payload.problem,
        baseline={
            name: observation.model_dump()
            for name, observation in payload.baseline.items()
        },
        status="detected",
    )
    db.add(case)
    db.flush()
    record_user_action(
        db,
        actor=user,
        action="improvement.case_created",
        object_type="improvement_case",
        object_id=case.id,
        after_data={"plant_id": case.plant_id, "status": "detected"},
    )
    db.commit()
    db.refresh(case)
    return _case_json(case)


@router.get("/{case_id}")
def get_improvement_case(
    case_id: str,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
) -> dict[str, Any]:
    return _case_json(_get_case(db, user, case_id))


@router.patch("/{case_id}")
def update_improvement_case(
    case_id: str,
    payload: ImprovementCaseUpdate,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin", "operator")),
) -> dict[str, Any]:
    case = _get_case(db, user, case_id)
    updates = payload.model_dump(exclude_unset=True)
    previous_status = _case_status(case)

    owner_id = updates.get("owner_user_id")
    if owner_id is not None:
        owner = db.get(AlertUser, owner_id)
        if owner is None or not owner.is_active:
            raise HTTPException(
                status_code=422,
                detail="Người phụ trách không hợp lệ.",
            )
        require_plant_access(db, owner, case.plant_id, actor=user)
    if "implemented_at" in updates and updates["implemented_at"] is not None:
        if not case.action or not case.root_cause:
            raise HTTPException(
                status_code=409,
                detail="Cần ghi nhận nguyên nhân và hành động trước khi triển khai.",
            )
        if updates["implemented_at"].tzinfo is None:
            updates["implemented_at"] = updates["implemented_at"].replace(
                tzinfo=timezone.utc
            )
    if updates.get("after_measurement") is not None:
        if not case.implemented_at and updates.get("implemented_at") is None:
            raise HTTPException(
                status_code=409,
                detail="Cần ghi nhận thời điểm triển khai trước khi đo lường.",
            )
        if not set(case.baseline).intersection(
            updates["after_measurement"]
        ):
            raise HTTPException(
                status_code=422,
                detail="KPI sau can thiệp phải trùng ít nhất một KPI baseline.",
            )
    if updates.get("conclusion") and not (
        case.after_measurement or updates.get("after_measurement")
    ):
        raise HTTPException(
            status_code=409,
            detail="Cần có số liệu sau cải tiến trước khi đánh giá.",
        )
    if updates.get("close") is True and not (
        case.conclusion or updates.get("conclusion")
    ):
        raise HTTPException(
            status_code=409,
            detail="Chỉ có thể đóng hồ sơ sau khi đã đánh giá kết quả.",
        )

    updates.pop("close", None)
    for key, value in updates.items():
        if key == "confounders" and value is None:
            value = []
        setattr(case, key, value)
    if payload.close is True:
        case.closed_at = datetime.now(timezone.utc)
    elif payload.close is False:
        case.closed_at = None
    if case.implemented_at and (not case.root_cause or not case.action):
        raise HTTPException(
            status_code=409,
            detail="Không thể xóa nguyên nhân hoặc hành động đã triển khai.",
        )
    if case.after_measurement and not case.implemented_at:
        raise HTTPException(
            status_code=409,
            detail="Không thể xóa thời điểm triển khai khi đã có số liệu sau cải tiến.",
        )
    if case.conclusion and not case.after_measurement:
        raise HTTPException(
            status_code=409,
            detail="Không thể xóa số liệu sau cải tiến khi đã có kết luận.",
        )
    if case.closed_at and not case.conclusion:
        raise HTTPException(
            status_code=409,
            detail="Không thể đóng hồ sơ khi chưa có kết luận.",
        )
    case.status = _case_status(case)
    record_user_action(
        db,
        actor=user,
        action="improvement.case_updated",
        object_type="improvement_case",
        object_id=case.id,
        before_data={"status": previous_status},
        after_data={"status": case.status, "fields": sorted(updates)},
    )
    db.commit()
    db.refresh(case)
    return _case_json(case)
