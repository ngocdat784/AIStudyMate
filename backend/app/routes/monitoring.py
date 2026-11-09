import hmac
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Literal

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
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.monitoring import (
    Alert,
    AlertDelivery,
    AlertNotification,
    AlertRule,
    AlertStatusHistory,
    AlertUser,
    AlertUserPlant,
    MonitoringDataset,
    Plant,
    ProductionLine,
)
from app.services.monitoring_auth import (
    create_access_token,
    ensure_auth_configured,
    get_current_user,
    hash_password,
    accessible_plant_ids,
    has_plant_access,
    require_plant_access,
    require_roles,
    verify_password,
)
from app.services.monitoring_service import (
    SCHEDULE_SECONDS,
    evaluate_monitoring_dataset,
    notification_channel_status,
    record_alert_event,
)
from app.config import settings


router = APIRouter(prefix="/api/monitoring", tags=["Intelligent Monitoring"])
logger = logging.getLogger(__name__)


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("Email không hợp lệ.")
        return normalized


class SignupRequest(Credentials):
    display_name: str = Field(min_length=1, max_length=120)


class BootstrapRequest(SignupRequest):
    bootstrap_token: str = Field(min_length=1, max_length=512)


class AdminUserRequest(SignupRequest):
    role: Literal["operator", "viewer"] = "operator"


class PlantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    timezone: str = Field(default="UTC", min_length=1, max_length=80)


class PlantUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    timezone: str | None = Field(default=None, min_length=1, max_length=80)


class LineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plant_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=160)


class LineUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)


class DatasetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    plant_id: int = Field(gt=0)
    line_id: int | None = Field(default=None, gt=0)
    granularity: Literal["day", "week", "month"] = "day"
    schedule_interval: int = Field(default=1, ge=1, le=52)
    schedule_unit: Literal["hours", "days", "weeks"] = "days"
    data_rows: list[dict] = Field(min_length=1, max_length=100000)


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interval: int = Field(ge=1, le=52)
    unit: Literal["hours", "days", "weeks"]
    enabled: bool = True


class RuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    plant_id: int = Field(gt=0)
    line_id: int | None = Field(default=None, gt=0)
    product: str | None = Field(default=None, max_length=200)
    metric: Literal["production", "defects", "defect_rate", "downtime"]
    operator: Literal["gt", "gte", "lt", "lte"]
    threshold: float
    severity: Literal["low", "medium", "high", "critical"] = "high"
    enabled: bool = True

    @field_validator("threshold")
    @classmethod
    def finite_threshold(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Ngưỡng phải là số hữu hạn.")
        return value


class AlertUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "open",
        "acknowledged",
        "investigating",
        "snoozed",
        "resolved",
        "closed",
    ] | None = None
    assigned_to: int | None = None
    snoozed_until: datetime | None = None
    note: str | None = Field(default=None, max_length=1000)
    feedback: Literal["useful", "false_positive", "duplicate"] | None = None
    feedback_note: str | None = Field(default=None, max_length=1000)


ALERT_TRANSITIONS = {
    "open": {"acknowledged", "investigating", "snoozed", "resolved", "closed"},
    "acknowledged": {"open", "investigating", "snoozed", "resolved", "closed"},
    "investigating": {"open", "acknowledged", "snoozed", "resolved", "closed"},
    "snoozed": {"open", "acknowledged", "investigating", "resolved", "closed"},
    "resolved": {"open", "closed"},
    "closed": {"open"},
}


class RoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["admin", "operator", "viewer"]
    is_active: bool | None = None


class PlantAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plant_ids: list[int] = Field(max_length=1000)


class EmailPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


def _require_user_scope(
    db: Session,
    user: AlertUser,
    plant_id: int,
    line_id: int | None = None,
) -> None:
    _require_scope(db, plant_id, line_id)
    require_plant_access(db, user, plant_id)


def _filter_plant_scope(query, user: AlertUser, db: Session, column):
    plant_ids = accessible_plant_ids(db, user)
    if plant_ids is None:
        return query
    return query.where(column.in_(plant_ids)) if plant_ids else query.where(False)


def _user_json(user: AlertUser) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
        "email_notifications": user.email_notifications,
    }


def _plant_json(plant: Plant) -> dict:
    return {"id": plant.id, "name": plant.name, "timezone": plant.timezone}


def _dataset_json(dataset: MonitoringDataset) -> dict:
    return {
        "id": dataset.id,
        "name": dataset.name,
        "plant_id": dataset.plant_id,
        "line_id": dataset.line_id,
        "granularity": dataset.granularity,
        "schedule_interval": dataset.schedule_interval,
        "schedule_unit": dataset.schedule_unit,
        "enabled": dataset.enabled,
        "last_checked_at": dataset.last_checked_at,
        "created_at": dataset.created_at,
        "row_count": len(dataset.data_rows),
    }


def _alert_json(alert: Alert, db: Session) -> dict:
    assigned = db.get(AlertUser, alert.assigned_to) if alert.assigned_to else None
    plant = db.get(Plant, alert.plant_id)
    line = db.get(ProductionLine, alert.line_id) if alert.line_id else None
    return {
        "id": alert.id,
        "alert_type": alert.alert_type,
        "rule_id": alert.rule_id,
        "plant_id": alert.plant_id,
        "plant_name": plant.name if plant else None,
        "line_id": alert.line_id,
        "line_name": line.name if line else None,
        "metric": alert.metric,
        "scope_type": alert.scope_type,
        "scope_value": alert.scope_value,
        "severity": alert.severity,
        "title": alert.title,
        "summary": alert.summary,
        "ai_explanation": alert.ai_explanation,
        "ai_explanation_status": alert.ai_explanation_status,
        "observed_value": alert.observed_value,
        "threshold_value": alert.threshold_value,
        "impact": alert.impact,
        "evidence": alert.evidence,
        "recommended_check": alert.recommended_check,
        "confidence": alert.confidence,
        "status": alert.status,
        "feedback": alert.feedback,
        "feedback_note": alert.feedback_note,
        "assigned_to": alert.assigned_to,
        "assigned_name": assigned.display_name if assigned else None,
        "detected_at": alert.detected_at,
        "last_seen_at": alert.last_seen_at,
        "resolved_at": alert.resolved_at,
        "snoozed_until": alert.snoozed_until,
    }


def _require_scope(db: Session, plant_id: int, line_id: int | None) -> None:
    if db.get(Plant, plant_id) is None:
        raise HTTPException(status_code=404, detail="Nhà máy không tồn tại.")
    if line_id is not None:
        line = db.get(ProductionLine, line_id)
        if line is None or line.plant_id != plant_id:
            raise HTTPException(
                status_code=422,
                detail="Dây chuyền không thuộc nhà máy đã chọn.",
            )


@router.post("/auth/signup", status_code=status.HTTP_201_CREATED)
def signup(request: SignupRequest, db: Session = Depends(get_db)):
    ensure_auth_configured()
    user = AlertUser(
        email=request.email,
        display_name=request.display_name.strip(),
        password_hash=hash_password(request.password),
        role="viewer",
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email đã được đăng ký.") from exc
    db.refresh(user)
    return {"access_token": create_access_token(user), "token_type": "bearer", "user": _user_json(user)}


@router.post("/auth/login")
def login(request: Credentials, db: Session = Depends(get_db)):
    ensure_auth_configured()
    user = db.scalar(select(AlertUser).where(AlertUser.email == request.email))
    if user is None or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email hoặc mật khẩu không đúng.")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Tài khoản đã bị vô hiệu hóa.")
    return {"access_token": create_access_token(user), "token_type": "bearer", "user": _user_json(user)}


@router.post("/auth/bootstrap-admin", status_code=status.HTTP_201_CREATED)
def bootstrap_admin(request: BootstrapRequest, db: Session = Depends(get_db)):
    ensure_auth_configured()
    configured_token = settings.ALERT_BOOTSTRAP_TOKEN
    if not configured_token or not hmac.compare_digest(
        request.bootstrap_token, configured_token
    ):
        raise HTTPException(status_code=403, detail="Bootstrap token không hợp lệ.")
    existing_admin = db.scalar(
        select(AlertUser.id).where(AlertUser.role == "admin").limit(1)
    )
    if existing_admin is not None:
        raise HTTPException(status_code=409, detail="Admin đầu tiên đã được khởi tạo.")
    user = AlertUser(
        email=request.email,
        display_name=request.display_name.strip(),
        password_hash=hash_password(request.password),
        role="admin",
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email đã được đăng ký.") from exc
    db.refresh(user)
    return {"access_token": create_access_token(user), "token_type": "bearer", "user": _user_json(user)}


@router.get("/auth/me")
def current_user(user: AlertUser = Depends(get_current_user)):
    return _user_json(user)


@router.get("/users")
def list_users(
    db: Session = Depends(get_db),
    plant_id: int | None = Query(default=None, gt=0),
    current: AlertUser = Depends(get_current_user),
):
    query = select(AlertUser).where(AlertUser.is_active.is_(True))
    if current.role != "admin":
        if plant_id is not None:
            require_plant_access(db, current, plant_id)
            plant_ids = [plant_id]
        else:
            plant_ids = accessible_plant_ids(db, current) or []
        scoped_user_ids = select(AlertUserPlant.user_id).where(
            AlertUserPlant.plant_id.in_(plant_ids)
        )
        query = query.where(
            or_(
                AlertUser.role == "admin",
                AlertUser.id.in_(scoped_user_ids),
            )
        )
    elif plant_id is not None:
        _require_scope(db, plant_id, None)
        scoped_user_ids = select(AlertUserPlant.user_id).where(
            AlertUserPlant.plant_id == plant_id
        )
        query = query.where(
            or_(
                AlertUser.role == "admin",
                AlertUser.id.in_(scoped_user_ids),
            )
        )
    users = db.scalars(query.order_by(AlertUser.display_name)).all()
    return [
        {
            "id": user.id,
            "display_name": user.display_name,
            "role": user.role,
        }
        for user in users
    ]


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    request: AdminUserRequest,
    db: Session = Depends(get_db),
    _: AlertUser = Depends(require_roles("admin")),
):
    ensure_auth_configured()
    user = AlertUser(
        email=request.email,
        display_name=request.display_name.strip(),
        password_hash=hash_password(request.password),
        role=request.role,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email đã được đăng ký.") from exc
    db.refresh(user)
    return _user_json(user)


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    request: RoleUpdate,
    db: Session = Depends(get_db),
    _: AlertUser = Depends(require_roles("admin")),
):
    users = db.scalars(
        select(AlertUser)
        .order_by(AlertUser.id)
        .with_for_update()
    ).all()
    user = next((item for item in users if item.id == user_id), None)
    if user is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng.")
    new_role = request.role
    new_is_active = (
        user.is_active
        if request.is_active is None
        else request.is_active
    )
    loses_active_admin = (
        user.role == "admin"
        and user.is_active
        and (new_role != "admin" or not new_is_active)
    )
    if loses_active_admin:
        active_admin_count = sum(
            item.role == "admin" and item.is_active
            for item in users
        )
        if active_admin_count <= 1:
            raise HTTPException(
                status_code=409,
                detail="Không thể hạ quyền hoặc vô hiệu hóa Admin hoạt động cuối cùng.",
            )

    user.role = new_role
    user.is_active = new_is_active
    if new_role == "admin":
        db.execute(
            delete(AlertUserPlant).where(AlertUserPlant.user_id == user.id)
        )
    db.commit()
    db.refresh(user)
    return _user_json(user)


@router.get("/users/{user_id}/plants")
def list_user_plants(
    user_id: int,
    db: Session = Depends(get_db),
    current: AlertUser = Depends(get_current_user),
):
    if current.role != "admin" and current.id != user_id:
        raise HTTPException(status_code=403, detail="Không có quyền xem phân công này.")
    target = db.scalar(
        select(AlertUser)
        .where(AlertUser.id == user_id)
        .with_for_update()
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng.")
    if target.role == "admin":
        plants = db.scalars(select(Plant).order_by(Plant.name)).all()
    else:
        plants = db.scalars(
            select(Plant)
            .join(AlertUserPlant, AlertUserPlant.plant_id == Plant.id)
            .where(AlertUserPlant.user_id == user_id)
            .order_by(Plant.name)
        ).all()
    return [_plant_json(plant) for plant in plants]


@router.put("/users/{user_id}/plants")
def assign_user_plants(
    user_id: int,
    request: PlantAssignmentRequest,
    db: Session = Depends(get_db),
    actor: AlertUser = Depends(require_roles("admin")),
):
    target = db.get(AlertUser, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng.")
    if target.role == "admin" and request.plant_ids:
        raise HTTPException(
            status_code=422,
            detail="Admin có quyền toàn hệ thống và không cần phân công nhà máy.",
        )
    if len(set(request.plant_ids)) != len(request.plant_ids):
        raise HTTPException(status_code=422, detail="Danh sách Plant bị trùng.")
    plants = db.scalars(
        select(Plant).where(Plant.id.in_(request.plant_ids))
    ).all() if request.plant_ids else []
    if len(plants) != len(request.plant_ids):
        raise HTTPException(status_code=422, detail="Có nhà máy không tồn tại.")

    existing = db.scalars(
        select(AlertUserPlant).where(AlertUserPlant.user_id == target.id)
    ).all()
    by_plant = {assignment.plant_id: assignment for assignment in existing}
    requested_ids = set(request.plant_ids)
    for assignment in existing:
        if assignment.plant_id not in requested_ids:
            db.delete(assignment)
    for plant_id in request.plant_ids:
        if plant_id not in by_plant:
            db.add(
                AlertUserPlant(
                    user_id=target.id,
                    plant_id=plant_id,
                    assigned_by=actor.id,
                )
            )
    db.commit()
    return {"user_id": target.id, "plant_ids": sorted(requested_ids)}


@router.get("/plants")
def list_plants(
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
):
    query = select(Plant).order_by(Plant.name)
    query = _filter_plant_scope(query, user, db, Plant.id)
    plants = db.scalars(query).all()
    return [
        {
            **_plant_json(plant),
            "lines": [
                {"id": line.id, "name": line.name}
                for line in db.scalars(
                    select(ProductionLine)
                    .where(ProductionLine.plant_id == plant.id)
                    .order_by(ProductionLine.name)
                ).all()
            ],
        }
        for plant in plants
    ]


@router.post("/plants", status_code=status.HTTP_201_CREATED)
def create_plant(
    request: PlantRequest,
    db: Session = Depends(get_db),
    _: AlertUser = Depends(require_roles("admin")),
):
    plant = Plant(name=request.name.strip(), timezone=request.timezone.strip())
    db.add(plant)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Tên nhà máy đã tồn tại.") from exc
    db.refresh(plant)
    return _plant_json(plant)


@router.patch("/plants/{plant_id}")
def update_plant(
    plant_id: int,
    request: PlantUpdate,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin", "operator")),
):
    require_plant_access(db, user, plant_id)
    plant = db.get(Plant, plant_id)
    if plant is None:
        raise HTTPException(status_code=404, detail="Nhà máy không tồn tại.")
    if request.name is not None:
        plant.name = request.name.strip()
    if request.timezone is not None:
        plant.timezone = request.timezone.strip()
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Tên nhà máy đã tồn tại.") from exc
    return _plant_json(plant)


@router.delete("/plants/{plant_id}")
def delete_plant(
    plant_id: int,
    db: Session = Depends(get_db),
    _: AlertUser = Depends(require_roles("admin")),
):
    plant = db.get(Plant, plant_id)
    if plant is None:
        raise HTTPException(status_code=404, detail="Nhà máy không tồn tại.")
    db.delete(plant)
    db.commit()
    return {"deleted_id": plant_id}


@router.post("/lines", status_code=status.HTTP_201_CREATED)
def create_line(
    request: LineRequest,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin", "operator")),
):
    _require_user_scope(db, user, request.plant_id)
    line = ProductionLine(plant_id=request.plant_id, name=request.name.strip())
    db.add(line)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Tên dây chuyền đã tồn tại tại nhà máy.") from exc
    db.refresh(line)
    return {"id": line.id, "plant_id": line.plant_id, "name": line.name}


@router.patch("/lines/{line_id}")
def update_line(
    line_id: int,
    request: LineUpdate,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin", "operator")),
):
    line = db.get(ProductionLine, line_id)
    if line is None:
        raise HTTPException(status_code=404, detail="Dây chuyền không tồn tại.")
    require_plant_access(db, user, line.plant_id)
    line.name = request.name.strip()
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Tên dây chuyền đã tồn tại tại nhà máy."
        ) from exc
    return {"id": line.id, "plant_id": line.plant_id, "name": line.name}


@router.delete("/lines/{line_id}")
def delete_line(
    line_id: int,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin", "operator")),
):
    line = db.get(ProductionLine, line_id)
    if line is None:
        raise HTTPException(status_code=404, detail="Dây chuyền không tồn tại.")
    require_plant_access(db, user, line.plant_id)
    db.delete(line)
    db.commit()
    return {"deleted_id": line_id}


@router.get("/datasets")
def list_datasets(
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
):
    query = _filter_plant_scope(
        select(MonitoringDataset), user, db, MonitoringDataset.plant_id
    )
    datasets = db.scalars(
        query.order_by(MonitoringDataset.created_at.desc()).limit(250)
    ).all()
    return [_dataset_json(dataset) for dataset in datasets]


@router.delete("/datasets/{dataset_id}")
def delete_dataset(
    dataset_id: int,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin")),
):
    dataset = db.get(MonitoringDataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy snapshot.")
    require_plant_access(db, user, dataset.plant_id)
    newer = db.scalar(
        select(MonitoringDataset.id)
        .where(
            MonitoringDataset.plant_id == dataset.plant_id,
            MonitoringDataset.line_id == dataset.line_id,
            MonitoringDataset.id != dataset.id,
            or_(
                MonitoringDataset.created_at > dataset.created_at,
                and_(
                    MonitoringDataset.created_at == dataset.created_at,
                    MonitoringDataset.id > dataset.id,
                ),
            ),
        )
        .limit(1)
    )
    if newer is None:
        db.query(MonitoringDataset).filter(
            MonitoringDataset.plant_id == dataset.plant_id,
            MonitoringDataset.line_id == dataset.line_id,
        ).update({"enabled": False}, synchronize_session=False)
    db.delete(dataset)
    db.commit()
    return {
        "deleted_id": dataset_id,
        "monitoring_paused": newer is None,
    }


@router.post("/datasets", status_code=status.HTTP_201_CREATED)
def create_dataset(
    request: DatasetRequest,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin", "operator")),
):
    _require_user_scope(db, user, request.plant_id, request.line_id)
    if len(request.data_rows) > 100000:
        raise HTTPException(status_code=413, detail="Snapshot vượt quá 100.000 dòng.")
    dataset = MonitoringDataset(
        name=request.name.strip(),
        plant_id=request.plant_id,
        line_id=request.line_id,
        uploaded_by=user.id,
        granularity=request.granularity,
        schedule_interval=request.schedule_interval,
        schedule_unit=request.schedule_unit,
        enabled=True,
        data_rows=request.data_rows,
    )
    db.add(dataset)
    db.flush()
    try:
        result = evaluate_monitoring_dataset(db, dataset)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.refresh(dataset)
    return {**_dataset_json(dataset), **result}


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_monitoring_snapshot(
    files: list[UploadFile] = File(...),
    plant_id: int = Form(..., gt=0),
    line_id: int | None = Form(default=None, gt=0),
    granularity: Literal["day", "week", "month"] = Form(default="day"),
    schedule_interval: int = Form(default=1, ge=1, le=52),
    schedule_unit: Literal["hours", "days", "weeks"] = Form(default="days"),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin", "operator")),
):
    if not files or len(files) > 10:
        raise HTTPException(status_code=400, detail="Chọn từ 1 đến 10 file dữ liệu.")
    _require_user_scope(db, user, plant_id, line_id)
    from app.routes.hardware import _json_safe_cell, _read_hardware_upload
    from app.services.multi_file_service import (
        merge_normalized_hardware_files,
        normalize_hardware_files,
    )

    upload_results = []
    for uploaded in files:
        filename = uploaded.filename or "File không tên"
        try:
            dataframe = await _read_hardware_upload(uploaded)
        except HTTPException as exc:
            upload_results.append({"filename": filename, "error": exc.detail})
        except Exception as exc:
            logger.exception(
                "Could not read monitoring upload %r",
                uploaded.filename,
            )
            upload_results.append(
                {"filename": filename, "error": f"Không thể đọc file: {exc}"}
            )
        else:
            upload_results.append({"filename": filename, "dataframe": dataframe})

    normalized = normalize_hardware_files(upload_results)
    merged = merge_normalized_hardware_files(normalized)
    if merged.report["files_merged"] == 0:
        failures = [
            item
            for item in merged.report.get("file_reports", [])
            if item.get("status") != "merged"
        ]
        raise HTTPException(
            status_code=422,
            detail={"message": "Không có file dữ liệu hợp lệ.", "files": failures},
        )
    rows = [
        {
            column: _json_safe_cell(value)
            for column, value in row.items()
        }
        for row in merged.dataframe.to_dict(orient="records")
    ]
    dataset = MonitoringDataset(
        name=", ".join(upload.filename or "dataset" for upload in files)[:200],
        plant_id=plant_id,
        line_id=line_id,
        uploaded_by=user.id,
        granularity=granularity,
        schedule_interval=schedule_interval,
        schedule_unit=schedule_unit,
        enabled=True,
        data_rows=rows,
    )
    db.add(dataset)
    db.flush()
    try:
        evaluation = evaluate_monitoring_dataset(db, dataset)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.refresh(dataset)
    return {
        **_dataset_json(dataset),
        **evaluation,
        "files_merged": merged.report["files_merged"],
        "rows_merged": len(rows),
    }


@router.patch("/datasets/{dataset_id}/schedule")
def update_schedule(
    dataset_id: int,
    request: ScheduleRequest,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin", "operator")),
):
    if request.unit not in SCHEDULE_SECONDS:
        raise HTTPException(status_code=422, detail="Nhịp lịch không hợp lệ.")
    dataset = db.get(MonitoringDataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy snapshot.")
    require_plant_access(db, user, dataset.plant_id)
    dataset.schedule_interval = request.interval
    dataset.schedule_unit = request.unit
    dataset.enabled = request.enabled
    db.commit()
    return _dataset_json(dataset)


@router.post("/datasets/{dataset_id}/evaluate")
def evaluate_dataset_now(
    dataset_id: int,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin", "operator")),
):
    dataset = db.get(MonitoringDataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy snapshot.")
    require_plant_access(db, user, dataset.plant_id)
    try:
        result = evaluate_monitoring_dataset(db, dataset)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result


@router.get("/rules")
def list_rules(
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
):
    query = _filter_plant_scope(select(AlertRule), user, db, AlertRule.plant_id)
    rules = db.scalars(query.order_by(AlertRule.created_at.desc())).all()
    return [
        {
            "id": rule.id,
            "name": rule.name,
            "plant_id": rule.plant_id,
            "line_id": rule.line_id,
            "product": rule.product,
            "metric": rule.metric,
            "operator": rule.operator,
            "threshold": rule.threshold,
            "severity": rule.severity,
            "enabled": rule.enabled,
        }
        for rule in rules
    ]


@router.post("/rules", status_code=status.HTTP_201_CREATED)
def create_rule(
    request: RuleRequest,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin", "operator")),
):
    _require_user_scope(db, user, request.plant_id, request.line_id)
    rule = AlertRule(
        **request.model_dump(),
        created_by=user.id,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return {
        "id": rule.id,
        "name": rule.name,
        "plant_id": rule.plant_id,
        "line_id": rule.line_id,
        "product": rule.product,
        "metric": rule.metric,
        "operator": rule.operator,
        "threshold": rule.threshold,
        "severity": rule.severity,
        "enabled": rule.enabled,
    }


@router.patch("/rules/{rule_id}")
def update_rule(
    rule_id: int,
    request: RuleRequest,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin", "operator")),
):
    rule = db.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy rule.")
    _require_user_scope(db, user, rule.plant_id)
    _require_user_scope(db, user, request.plant_id, request.line_id)
    for field, value in request.model_dump().items():
        setattr(rule, field, value)
    db.commit()
    return {
        "id": rule.id,
        "name": rule.name,
        "plant_id": rule.plant_id,
        "line_id": rule.line_id,
        "product": rule.product,
        "metric": rule.metric,
        "operator": rule.operator,
        "threshold": rule.threshold,
        "severity": rule.severity,
        "enabled": rule.enabled,
    }


@router.delete("/rules/{rule_id}")
def delete_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin", "operator")),
):
    rule = db.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy rule.")
    require_plant_access(db, user, rule.plant_id)
    db.delete(rule)
    db.commit()
    return {"deleted_id": rule_id}


@router.get("/alerts")
def list_alerts(
    status_filter: Literal[
        "open",
        "acknowledged",
        "investigating",
        "snoozed",
        "resolved",
        "closed",
    ] | None = Query(
        default=None, alias="status"
    ),
    alert_type: Literal["threshold", "statistical"] | None = None,
    plant_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
):
    query = select(Alert)
    if plant_id is not None:
        require_plant_access(db, user, plant_id)
        query = query.where(Alert.plant_id == plant_id)
    else:
        query = _filter_plant_scope(query, user, db, Alert.plant_id)
    if status_filter:
        query = query.where(Alert.status == status_filter)
    if alert_type:
        query = query.where(Alert.alert_type == alert_type)
    query = query.order_by(Alert.detected_at.desc()).limit(limit)
    return [_alert_json(alert, db) for alert in db.scalars(query).all()]


@router.patch("/alerts/{alert_id}")
def update_alert(
    alert_id: int,
    request: AlertUpdate,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(require_roles("admin", "operator")),
):
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy cảnh báo.")
    require_plant_access(db, user, alert.plant_id)

    if "status" in request.model_fields_set and request.status is not None:
        target_status = request.status
        previous_snoozed_until = alert.snoozed_until
        if (
            previous_snoozed_until is not None
            and previous_snoozed_until.tzinfo is None
        ):
            previous_snoozed_until = previous_snoozed_until.replace(
                tzinfo=timezone.utc
            )
        if (
            target_status != alert.status
            and target_status not in ALERT_TRANSITIONS[alert.status]
        ):
            raise HTTPException(
                status_code=409,
                detail=f"Không thể chuyển Alert từ {alert.status} sang {target_status}.",
            )
        if target_status == "snoozed":
            snoozed_until = request.snoozed_until
            if snoozed_until is None or snoozed_until.tzinfo is None:
                raise HTTPException(
                    status_code=422,
                    detail="Khi tạm hoãn cần cung cấp snoozed_until có múi giờ.",
                )
            snoozed_until = snoozed_until.astimezone(timezone.utc)
            if snoozed_until <= datetime.now(timezone.utc):
                raise HTTPException(
                    status_code=422,
                    detail="Thời điểm kết thúc tạm hoãn phải ở tương lai.",
                )
            alert.snoozed_until = snoozed_until
        elif "snoozed_until" in request.model_fields_set:
            raise HTTPException(
                status_code=422,
                detail="Chỉ được đặt snoozed_until khi chuyển sang snoozed.",
            )

        previous_status = alert.status
        if target_status != previous_status:
            alert.status = target_status
            if target_status in ("resolved", "closed"):
                alert.resolved_at = datetime.now(timezone.utc)
            elif target_status != "snoozed":
                alert.snoozed_until = None
            record_alert_event(
                db,
                alert,
                event_type=(
                    "reopened"
                    if previous_status in ("resolved", "closed")
                    and target_status == "open"
                    else "status_changed"
                ),
                from_status=previous_status,
                to_status=target_status,
                actor_user_id=user.id,
                note=request.note,
                event_data=(
                    {"snoozed_until": alert.snoozed_until.isoformat()}
                    if alert.snoozed_until
                    else None
                ),
            )
        elif (
            target_status == "snoozed"
            and previous_snoozed_until != snoozed_until
        ):
            record_alert_event(
                db,
                alert,
                event_type="snooze_updated",
                from_status="snoozed",
                to_status="snoozed",
                actor_user_id=user.id,
                note=request.note,
                event_data={"snoozed_until": snoozed_until.isoformat()},
            )
    elif "snoozed_until" in request.model_fields_set:
        raise HTTPException(
            status_code=422,
            detail="Cần chuyển trạng thái sang snoozed để đặt snoozed_until.",
        )
    old_assigned_to = alert.assigned_to
    if request.assigned_to is not None:
        assignee = db.get(AlertUser, request.assigned_to)
        if (
            assignee is None
            or not assignee.is_active
            or assignee.role not in ("admin", "operator")
        ):
            raise HTTPException(status_code=422, detail="Người được giao không hợp lệ.")
        if assignee.role != "admin":
            require_plant_access(db, assignee, alert.plant_id)
        alert.assigned_to = assignee.id
    elif "assigned_to" in request.model_fields_set:
        alert.assigned_to = None
    if old_assigned_to != alert.assigned_to:
        record_alert_event(
            db,
            alert,
            event_type="assignment_changed",
            from_status=alert.status,
            to_status=alert.status,
            actor_user_id=user.id,
            note=request.note,
            event_data={
                "previous_assigned_to": old_assigned_to,
                "assigned_to": alert.assigned_to,
            },
        )
    if "feedback" in request.model_fields_set:
        old_feedback = alert.feedback
        alert.feedback = request.feedback
        if old_feedback != alert.feedback:
            record_alert_event(
                db,
                alert,
                event_type="feedback_changed",
                from_status=alert.status,
                to_status=alert.status,
                actor_user_id=user.id,
                note=request.feedback_note,
                event_data={
                    "previous_feedback": old_feedback,
                    "feedback": alert.feedback,
                },
            )
    if "feedback_note" in request.model_fields_set:
        alert.feedback_note = request.feedback_note
    db.commit()
    db.refresh(alert)
    return _alert_json(alert, db)


@router.get("/alerts/{alert_id}/history")
def alert_history(
    alert_id: int,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
):
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy cảnh báo.")
    require_plant_access(db, user, alert.plant_id)
    events = db.scalars(
        select(AlertStatusHistory)
        .where(AlertStatusHistory.alert_id == alert_id)
        .order_by(AlertStatusHistory.created_at, AlertStatusHistory.id)
    ).all()
    result = []
    for event in events:
        actor = db.get(AlertUser, event.actor_user_id) if event.actor_user_id else None
        result.append(
            {
                "id": event.id,
                "event_type": event.event_type,
                "from_status": event.from_status,
                "to_status": event.to_status,
                "actor_user_id": event.actor_user_id,
                "actor_name": actor.display_name if actor else "System",
                "note": event.note,
                "event_data": event.event_data,
                "created_at": event.created_at,
            }
        )
    return result


@router.get("/notifications")
def list_notifications(
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
):
    query = (
        select(AlertNotification)
        .join(Alert, Alert.id == AlertNotification.alert_id)
        .where(AlertNotification.user_id == user.id)
    )
    query = _filter_plant_scope(query, user, db, Alert.plant_id)
    notifications = db.scalars(
        query.order_by(AlertNotification.created_at.desc()).limit(100)
    ).all()
    return [
        {
            "id": item.id,
            "alert_id": item.alert_id,
            "message": item.message,
            "read": item.read_at is not None,
            "created_at": item.created_at,
        }
        for item in notifications
    ]


@router.get("/notifications/channels")
def notification_channels(
    _: AlertUser = Depends(get_current_user),
):
    return notification_channel_status()


@router.patch("/notifications/preferences")
def update_notification_preferences(
    request: EmailPreference,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
):
    user.email_notifications = request.enabled
    db.commit()
    return {"email_notifications": user.email_notifications}


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
):
    notification = db.get(AlertNotification, notification_id)
    if notification is None or notification.user_id != user.id:
        raise HTTPException(status_code=404, detail="Không tìm thấy thông báo.")
    alert = db.get(Alert, notification.alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy thông báo.")
    require_plant_access(db, user, alert.plant_id)
    notification.read_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": notification.id, "read": True}


@router.get("/dashboard")
def dashboard(
    days: int = Query(default=30, ge=7, le=365),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    plant_ids = accessible_plant_ids(db, user)

    def scoped_alert_query(query):
        if plant_ids is None:
            return query
        return query.where(Alert.plant_id.in_(plant_ids)) if plant_ids else query.where(False)

    total_query = scoped_alert_query(select(func.count(Alert.id)))
    total = db.scalar(total_query.where(Alert.detected_at >= since)) or 0
    open_count = db.scalar(
        scoped_alert_query(
            select(func.count(Alert.id)).where(Alert.status == "open")
        )
    ) or 0
    active_count = db.scalar(
        scoped_alert_query(
            select(func.count(Alert.id)).where(
                Alert.status.in_(("open", "acknowledged", "investigating", "snoozed"))
            )
        )
    ) or 0
    resolved_count = db.scalar(
        scoped_alert_query(
            select(func.count(Alert.id)).where(
                Alert.status.in_(("resolved", "closed")),
                Alert.resolved_at >= since,
            )
        )
    ) or 0
    by_type_query = scoped_alert_query(
        select(Alert.alert_type, func.count(Alert.id))
    )
    by_type = {
        row[0]: row[1]
        for row in db.execute(
            by_type_query.where(Alert.detected_at >= since)
            .group_by(Alert.alert_type)
        ).all()
    }
    by_severity_query = scoped_alert_query(
        select(Alert.severity, func.count(Alert.id))
    )
    by_severity = {
        row[0]: row[1]
        for row in db.execute(
            by_severity_query.where(
                Alert.detected_at >= since,
                Alert.status.not_in(("resolved", "closed")),
            )
            .group_by(Alert.severity)
        ).all()
    }
    resolved_durations = db.scalars(
        scoped_alert_query(select(Alert)).where(
            Alert.status.in_(("resolved", "closed")),
            Alert.resolved_at.is_not(None),
            Alert.resolved_at >= since,
        )
    ).all()
    delivery_stats = {
        f"{channel}:{delivery_status}": count
        for channel, delivery_status, count in db.execute(
            select(
                AlertDelivery.channel,
                AlertDelivery.status,
                func.count(AlertDelivery.id),
            )
            .join(Alert, Alert.id == AlertDelivery.alert_id)
            .where(
                True if plant_ids is None else Alert.plant_id.in_(plant_ids)
            )
            .group_by(AlertDelivery.channel, AlertDelivery.status)
        ).all()
    }
    feedback_counts = {
        feedback or "unrated": count
        for feedback, count in db.execute(
            scoped_alert_query(
                select(Alert.feedback, func.count(Alert.id))
            ).where(Alert.detected_at >= since)
            .group_by(Alert.feedback)
        ).all()
    }
    rated_count = (
        feedback_counts.get("useful", 0)
        + feedback_counts.get("false_positive", 0)
    )
    false_positive_rate = (
        feedback_counts.get("false_positive", 0) / rated_count
        if rated_count
        else None
    )
    average_resolution_hours = (
        sum(
            (alert.resolved_at - alert.detected_at).total_seconds() / 3600
            for alert in resolved_durations
            if alert.resolved_at
        )
        / len(resolved_durations)
        if resolved_durations
        else None
    )
    return {
        "period_days": days,
        "total_alerts": total,
        "open_alerts": open_count,
        "active_alerts": active_count,
        "resolved_alerts": resolved_count,
        "by_type": by_type,
        "by_severity": by_severity,
        "notification_channels": notification_channel_status(),
        "delivery_stats": delivery_stats,
        "feedback_counts": feedback_counts,
        "false_positive_rate": false_positive_rate,
        "average_resolution_hours": average_resolution_hours,
        "detection_policy": {
            "threshold": "Python rule evaluation",
            "statistical": "past-only rolling IQR + Z-score; minimum 8 baseline periods",
            "ai_decides_alert_existence": False,
        },
    }
