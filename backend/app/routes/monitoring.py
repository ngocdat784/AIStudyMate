import hmac
import hashlib
import logging
import math
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
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
    AlertUserDepartment,
    AlertUserFactory,
    AlertUser,
    AlertUserPlant,
    AuthSession,
    Department,
    Factory,
    MonitoringDataset,
    Organization,
    PasswordResetToken,
    Plant,
    ProductionLine,
)
from app.services.monitoring_auth import (
    LOGIN_FAILURE_LIMIT,
    LOGIN_LOCK_SECONDS,
    PASSWORD_RESET_TTL_SECONDS,
    create_access_token,
    ensure_auth_configured,
    get_current_user,
    get_current_session,
    hash_password,
    accessible_plant_ids,
    has_plant_access,
    require_plant_access,
    require_roles,
    revoke_user_sessions,
    send_password_reset_email,
    verify_password,
)
from app.services.monitoring_service import (
    SCHEDULE_SECONDS,
    evaluate_monitoring_dataset,
    notification_channel_status,
    record_alert_event,
)
from app.services.user_audit import record_user_action
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


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=12, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("Email không hợp lệ.")
        return normalized


class PasswordResetCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=32, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=120)


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
    factory_id: int = Field(gt=0)
    department_id: int | None = Field(default=None, gt=0)


class PlantUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    timezone: str | None = Field(default=None, min_length=1, max_length=80)
    factory_id: int | None = Field(default=None, gt=0)
    department_id: int | None = Field(default=None, gt=0)


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


class OrganizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=180)


class FactoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=180)


class DepartmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factory_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=180)


class OrganizationAssignmentsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factory_ids: list[int] = Field(max_length=1000)
    department_ids: list[int] = Field(max_length=1000)
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
        "account_status": user.account_status,
        "email_notifications": user.email_notifications,
        "last_login_at": user.last_login_at,
    }


def _plant_json(plant: Plant) -> dict:
    return {
        "id": plant.id,
        "name": plant.name,
        "timezone": plant.timezone,
        "factory_id": plant.factory_id,
        "department_id": plant.department_id,
    }


def _validate_plant_organization_scope(
    db: Session,
    factory_id: int,
    department_id: int | None,
) -> None:
    if db.get(Factory, factory_id) is None:
        raise HTTPException(status_code=404, detail="Factory không tồn tại.")
    if department_id is not None:
        department = db.get(Department, department_id)
        if department is None or department.factory_id != factory_id:
            raise HTTPException(
                status_code=422,
                detail="Department phải thuộc Factory đã chọn.",
            )


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
        db.flush()
        token = create_access_token(db, user)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email đã được đăng ký.") from exc
    db.refresh(user)
    return {"access_token": token, "token_type": "bearer", "user": _user_json(user)}


@router.post("/auth/login")
def login(
    request: Credentials,
    http_request: Request,
    db: Session = Depends(get_db),
):
    ensure_auth_configured()
    user = db.scalar(
        select(AlertUser)
        .where(AlertUser.email == request.email)
        .with_for_update()
    )
    if user is None:
        raise HTTPException(status_code=401, detail="Email hoặc mật khẩu không đúng.")
    now = datetime.now(timezone.utc)
    if user.account_status == "locked":
        locked_until = user.locked_until
        if locked_until is not None and locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        if locked_until is None or locked_until > now:
            raise HTTPException(
                status_code=423,
                detail="Tài khoản đang tạm khóa do đăng nhập thất bại quá nhiều lần.",
            )
        user.account_status = "active"
        user.failed_login_attempts = 0
        user.locked_until = None
    if not user.is_active or user.account_status == "inactive":
        raise HTTPException(status_code=403, detail="Tài khoản đã bị vô hiệu hóa.")
    if not verify_password(request.password, user.password_hash):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= LOGIN_FAILURE_LIMIT:
            user.account_status = "locked"
            user.locked_until = now + timedelta(seconds=LOGIN_LOCK_SECONDS)
            revoke_user_sessions(db, user.id)
            db.commit()
            raise HTTPException(
                status_code=423,
                detail="Tài khoản tạm khóa 15 phút do đăng nhập thất bại quá nhiều lần.",
            )
        db.commit()
        raise HTTPException(status_code=401, detail="Email hoặc mật khẩu không đúng.")
    user.failed_login_attempts = 0
    user.locked_until = None
    user.account_status = "active"
    user.last_login_at = now
    db.flush()
    token = create_access_token(db, user, http_request)
    db.commit()
    return {"access_token": token, "token_type": "bearer", "user": _user_json(user)}


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
        db.flush()
        token = create_access_token(db, user)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email đã được đăng ký.") from exc
    db.refresh(user)
    return {"access_token": token, "token_type": "bearer", "user": _user_json(user)}


@router.get("/auth/me")
def current_user(user: AlertUser = Depends(get_current_user)):
    return _user_json(user)


@router.patch("/auth/me")
def update_profile(
    request: ProfileUpdate,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
):
    previous_name = user.display_name
    user.display_name = request.display_name.strip()
    record_user_action(
        db,
        actor=user,
        action="account.profile_updated",
        object_type="alert_user",
        object_id=str(user.id),
        before_data={"display_name": previous_name},
        after_data={"display_name": user.display_name},
    )
    db.commit()
    return _user_json(user)


@router.post("/auth/logout")
def logout(
    current: tuple[AuthSession, AlertUser] = Depends(get_current_session),
    db: Session = Depends(get_db),
):
    session, _user = current
    session.revoked_at = datetime.now(timezone.utc)
    record_user_action(
        db,
        actor=_user,
        action="account.session_revoked",
        object_type="auth_session",
        object_id=str(session.id),
        after_data={"reason": "logout"},
    )
    db.commit()
    return {"logged_out": True}


@router.get("/auth/sessions")
def list_sessions(
    current: tuple[AuthSession, AlertUser] = Depends(get_current_session),
    db: Session = Depends(get_db),
):
    current_session, user = current
    sessions = db.scalars(
        select(AuthSession)
        .where(
            AuthSession.user_id == user.id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > datetime.now(timezone.utc),
        )
        .order_by(AuthSession.created_at.desc())
    ).all()
    return [
        {
            "id": session.id,
            "created_at": session.created_at,
            "last_seen_at": session.last_seen_at,
            "expires_at": session.expires_at,
            "current": session.id == current_session.id,
            "user_agent": session.user_agent,
        }
        for session in sessions
    ]


@router.delete("/auth/sessions/{session_id}")
def revoke_session(
    session_id: int,
    db: Session = Depends(get_db),
    current: AlertUser = Depends(get_current_user),
):
    session = db.scalar(
        select(AuthSession)
        .where(AuthSession.id == session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if session is None or session.user_id != current.id:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên đăng nhập.")
    if session.revoked_at is None:
        session.revoked_at = datetime.now(timezone.utc)
        record_user_action(
            db,
            actor=current,
            action="account.session_revoked",
            object_type="auth_session",
            object_id=str(session.id),
            after_data={"reason": "user_requested"},
        )
        db.commit()
    return {"revoked_id": session_id}


@router.post("/auth/change-password")
def change_password(
    request: PasswordChangeRequest,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
):
    if not verify_password(request.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Mật khẩu hiện tại không đúng.")
    if request.current_password == request.new_password:
        raise HTTPException(
            status_code=422,
            detail="Mật khẩu mới phải khác mật khẩu hiện tại.",
        )
    user.password_hash = hash_password(request.new_password)
    revoke_user_sessions(db, user.id)
    record_user_action(
        db,
        actor=user,
        action="account.password_changed",
        object_type="alert_user",
        object_id=str(user.id),
        after_data={"sessions_revoked": True},
    )
    db.commit()
    return {"password_changed": True, "sessions_revoked": True}


@router.post("/auth/forgot-password", status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(
    request: PasswordResetRequest,
    db: Session = Depends(get_db),
):
    if not notification_channel_status()["email"]:
        raise HTTPException(
            status_code=503,
            detail="Dịch vụ đặt lại mật khẩu qua email chưa được cấu hình.",
        )
    user = db.scalar(
        select(AlertUser).where(AlertUser.email == request.email)
    )
    if user is not None and user.account_status != "inactive":
        raw_token = secrets.token_urlsafe(48)
        token = PasswordResetToken(
            user_id=user.id,
            token_hash=hashlib.sha256(raw_token.encode("ascii")).hexdigest(),
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=PASSWORD_RESET_TTL_SECONDS),
        )
        db.add(token)
        db.flush()
        try:
            send_password_reset_email(user.email, raw_token)
        except Exception as exc:
            db.rollback()
            logger.exception("Could not send password reset email.")
            raise HTTPException(
                status_code=503,
                detail="Không thể gửi email đặt lại mật khẩu.",
            ) from exc
        db.commit()
    return {
        "message": (
            "Nếu tài khoản tồn tại, hướng dẫn đặt lại mật khẩu sẽ được gửi."
        )
    }


@router.post("/auth/reset-password")
def reset_password(
    request: PasswordResetCompleteRequest,
    db: Session = Depends(get_db),
):
    token_hash = hashlib.sha256(request.token.encode("ascii")).hexdigest()
    reset_token = db.scalar(
        select(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used_at.is_(None),
        )
        .with_for_update()
    )
    expires_at = reset_token.expires_at if reset_token is not None else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if reset_token is None or expires_at is None or expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Token đặt lại không hợp lệ hoặc hết hạn.")
    user = db.get(AlertUser, reset_token.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="Token đặt lại không hợp lệ.")
    reset_token.used_at = datetime.now(timezone.utc)
    user.password_hash = hash_password(request.new_password)
    user.failed_login_attempts = 0
    user.locked_until = None
    if user.is_active:
        user.account_status = "active"
    revoke_user_sessions(db, user.id)
    record_user_action(
        db,
        actor=user,
        action="account.password_reset",
        object_type="alert_user",
        object_id=str(user.id),
        after_data={"sessions_revoked": True},
    )
    db.commit()
    return {"password_reset": True, "sessions_revoked": True}


@router.get("/users")
def list_users(
    db: Session = Depends(get_db),
    plant_id: int | None = Query(default=None, gt=0),
    current: AlertUser = Depends(get_current_user),
):
    def scoped_user_ids(plant_ids: list[int]):
        return (
            select(AlertUserPlant.user_id)
            .join(Plant, Plant.id == AlertUserPlant.plant_id)
            .where(
                Plant.id.in_(plant_ids),
                Plant.factory_id.is_not(None),
                Plant.factory_id.in_(
                    select(AlertUserFactory.factory_id).where(
                        AlertUserFactory.user_id == AlertUserPlant.user_id
                    )
                ),
                or_(
                    Plant.department_id.is_(None),
                    Plant.department_id.in_(
                        select(AlertUserDepartment.department_id).where(
                            AlertUserDepartment.user_id == AlertUserPlant.user_id
                        )
                    ),
                ),
            )
        )

    query = select(AlertUser)
    if current.role != "admin":
        query = query.where(AlertUser.is_active.is_(True))
        if plant_id is not None:
            require_plant_access(db, current, plant_id)
            plant_ids = [plant_id]
        else:
            plant_ids = accessible_plant_ids(db, current) or []
        query = query.where(
            or_(
                AlertUser.role == "admin",
                AlertUser.id.in_(scoped_user_ids(plant_ids)),
            )
        )
    elif plant_id is not None:
        _require_scope(db, plant_id, None)
        query = query.where(
            or_(
                AlertUser.role == "admin",
                AlertUser.id.in_(scoped_user_ids([plant_id])),
            )
        )
    users = db.scalars(query.order_by(AlertUser.display_name)).all()
    return [
        {
            "id": user.id,
            "display_name": user.display_name,
            "role": user.role,
            "is_active": user.is_active,
            "account_status": user.account_status,
        }
        for user in users
    ]


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    request: AdminUserRequest,
    db: Session = Depends(get_db),
    actor: AlertUser = Depends(require_roles("admin")),
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
        db.flush()
        record_user_action(
            db,
            actor=actor,
            action="user.created",
            object_type="alert_user",
            object_id=str(user.id),
            after_data={"role": user.role, "is_active": user.is_active},
        )
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
    actor: AlertUser = Depends(require_roles("admin")),
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
        and user.account_status == "active"
        and (new_role != "admin" or not new_is_active)
    )
    if loses_active_admin:
        active_admin_count = sum(
            item.role == "admin"
            and item.is_active
            and item.account_status == "active"
            for item in users
        )
        if active_admin_count <= 1:
            raise HTTPException(
                status_code=409,
                detail="Không thể hạ quyền hoặc vô hiệu hóa Admin hoạt động cuối cùng.",
            )

    before_data = {
        "role": user.role,
        "is_active": user.is_active,
        "account_status": user.account_status,
    }
    credentials_changed = user.role != new_role or user.is_active != new_is_active
    user.role = new_role
    user.is_active = new_is_active
    if request.is_active is not None:
        user.account_status = "active" if new_is_active else "inactive"
        if new_is_active:
            user.failed_login_attempts = 0
            user.locked_until = None
    if credentials_changed:
        revoke_user_sessions(db, user.id)
    if new_role == "admin":
        db.execute(
            delete(AlertUserPlant).where(AlertUserPlant.user_id == user.id)
        )
        db.execute(
            delete(AlertUserFactory).where(AlertUserFactory.user_id == user.id)
        )
        db.execute(
            delete(AlertUserDepartment).where(
                AlertUserDepartment.user_id == user.id
            )
        )
    record_user_action(
        db,
        actor=actor,
        action="user.updated",
        object_type="alert_user",
        object_id=str(user.id),
        before_data=before_data,
        after_data={
            "role": user.role,
            "is_active": user.is_active,
            "account_status": user.account_status,
        },
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
    target = db.get(AlertUser, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng.")
    target_plant_ids = accessible_plant_ids(db, target)
    if target_plant_ids is None:
        plants = db.scalars(select(Plant).order_by(Plant.name)).all()
    else:
        plants = db.scalars(
            select(Plant)
            .where(Plant.id.in_(target_plant_ids))
            .order_by(Plant.name)
        ).all()
    return [_plant_json(plant) for plant in plants]


@router.get("/users/{user_id}/organization-scope")
def list_user_organization_scope(
    user_id: int,
    db: Session = Depends(get_db),
    current: AlertUser = Depends(get_current_user),
):
    if current.role != "admin" and current.id != user_id:
        raise HTTPException(
            status_code=403,
            detail="Không có quyền xem phân công này.",
        )
    target = db.scalar(
        select(AlertUser)
        .where(AlertUser.id == user_id)
        .with_for_update()
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng.")
    if target.role == "admin":
        return {"factory_ids": [], "department_ids": [], "plant_ids": []}
    return {
        "factory_ids": sorted(
            db.scalars(
                select(AlertUserFactory.factory_id).where(
                    AlertUserFactory.user_id == user_id
                )
            ).all()
        ),
        "department_ids": sorted(
            db.scalars(
                select(AlertUserDepartment.department_id).where(
                    AlertUserDepartment.user_id == user_id
                )
            ).all()
        ),
        "plant_ids": sorted(
            db.scalars(
                select(AlertUserPlant.plant_id).where(
                    AlertUserPlant.user_id == user_id
                )
            ).all()
        ),
    }


@router.put("/users/{user_id}/organization-scope")
def assign_user_organization_scope(
    user_id: int,
    request: OrganizationAssignmentsRequest,
    db: Session = Depends(get_db),
    actor: AlertUser = Depends(require_roles("admin")),
):
    target = db.scalar(
        select(AlertUser)
        .where(AlertUser.id == user_id)
        .with_for_update()
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng.")
    if target.role == "admin" and (
        request.factory_ids or request.department_ids or request.plant_ids
    ):
        raise HTTPException(
            status_code=422,
            detail="Admin có quyền toàn hệ thống và không cần phân công phạm vi.",
        )
    before_scope = {
        "factory_ids": sorted(
            db.scalars(
                select(AlertUserFactory.factory_id).where(
                    AlertUserFactory.user_id == target.id
                )
            ).all()
        ),
        "department_ids": sorted(
            db.scalars(
                select(AlertUserDepartment.department_id).where(
                    AlertUserDepartment.user_id == target.id
                )
            ).all()
        ),
        "plant_ids": sorted(
            db.scalars(
                select(AlertUserPlant.plant_id).where(
                    AlertUserPlant.user_id == target.id
                )
            ).all()
        ),
    }
    for values, label in (
        (request.factory_ids, "Factory"),
        (request.department_ids, "Department"),
        (request.plant_ids, "Plant"),
    ):
        if len(set(values)) != len(values):
            raise HTTPException(
                status_code=422,
                detail=f"Danh sách {label} bị trùng.",
            )

    factories = db.scalars(
        select(Factory).where(Factory.id.in_(request.factory_ids))
    ).all() if request.factory_ids else []
    departments = db.scalars(
        select(Department).where(Department.id.in_(request.department_ids))
    ).all() if request.department_ids else []
    plants = db.scalars(
        select(Plant).where(Plant.id.in_(request.plant_ids))
    ).all() if request.plant_ids else []
    if len(factories) != len(request.factory_ids):
        raise HTTPException(status_code=422, detail="Có Factory không tồn tại.")
    if len(departments) != len(request.department_ids):
        raise HTTPException(status_code=422, detail="Có Department không tồn tại.")
    if len(plants) != len(request.plant_ids):
        raise HTTPException(status_code=422, detail="Có Plant không tồn tại.")

    factory_ids = set(request.factory_ids)
    department_ids = set(request.department_ids)
    for department in departments:
        if department.factory_id not in factory_ids:
            raise HTTPException(
                status_code=422,
                detail="Department assignment yêu cầu Factory assignment tương ứng.",
            )
    for plant in plants:
        if plant.factory_id is None:
            raise HTTPException(
                status_code=422,
                detail="Plant chưa được đưa khỏi quarantine tổ chức.",
            )
        if plant.factory_id not in factory_ids:
            raise HTTPException(
                status_code=422,
                detail="Plant assignment yêu cầu Factory assignment tương ứng.",
            )
        if (
            plant.department_id is not None
            and plant.department_id not in department_ids
        ):
            raise HTTPException(
                status_code=422,
                detail="Plant thuộc Department cần có Department assignment.",
            )

    for model, column, values in (
        (AlertUserFactory, AlertUserFactory.factory_id, factory_ids),
        (AlertUserDepartment, AlertUserDepartment.department_id, department_ids),
        (AlertUserPlant, AlertUserPlant.plant_id, set(request.plant_ids)),
    ):
        existing = db.scalars(
            select(model).where(model.user_id == target.id)
        ).all()
        existing_by_scope = {getattr(item, column.key): item for item in existing}
        for scope_id, assignment in existing_by_scope.items():
            if scope_id not in values:
                db.delete(assignment)
        for scope_id in values - set(existing_by_scope):
            db.add(
                model(
                    user_id=target.id,
                    **{column.key: scope_id},
                    assigned_by=actor.id,
                )
            )
    record_user_action(
        db,
        actor=actor,
        action="user.organization_scope_updated",
        object_type="alert_user",
        object_id=str(target.id),
        before_data=before_scope,
        after_data={
            "factory_ids": sorted(factory_ids),
            "department_ids": sorted(department_ids),
            "plant_ids": sorted(set(request.plant_ids)),
        },
    )
    db.commit()
    return {
        "user_id": target.id,
        "factory_ids": sorted(factory_ids),
        "department_ids": sorted(department_ids),
        "plant_ids": sorted(set(request.plant_ids)),
    }


@router.put("/users/{user_id}/plants")
def assign_user_plants(
    user_id: int,
    request: PlantAssignmentRequest,
    db: Session = Depends(get_db),
    actor: AlertUser = Depends(require_roles("admin")),
):
    target = db.scalar(
        select(AlertUser)
        .where(AlertUser.id == user_id)
        .with_for_update()
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng.")
    if target.role == "admin" and request.plant_ids:
        raise HTTPException(
            status_code=422,
            detail="Admin có quyền toàn hệ thống và không cần phân công nhà máy.",
        )
    before_ids = sorted(
        db.scalars(
            select(AlertUserPlant.plant_id).where(
                AlertUserPlant.user_id == target.id
            )
        ).all()
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
    record_user_action(
        db,
        actor=actor,
        action="user.plant_scope_updated",
        object_type="alert_user",
        object_id=str(target.id),
        before_data={"plant_ids": before_ids},
        after_data={"plant_ids": sorted(requested_ids)},
    )
    db.commit()
    return {"user_id": target.id, "plant_ids": sorted(requested_ids)}


@router.get("/organizations")
def list_organizations(
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
):
    query = select(Organization)
    if user.role != "admin":
        accessible_factories = select(Plant.factory_id).where(
            Plant.id.in_(accessible_plant_ids(db, user) or [])
        )
        query = query.where(Organization.id.in_(
            select(Factory.organization_id).where(
                Factory.id.in_(accessible_factories)
            )
        ))
    return [
        {"id": organization.id, "name": organization.name}
        for organization in db.scalars(query.order_by(Organization.name)).all()
    ]


@router.post("/organizations", status_code=status.HTTP_201_CREATED)
def create_organization(
    request: OrganizationRequest,
    db: Session = Depends(get_db),
    _: AlertUser = Depends(require_roles("admin")),
):
    organization = Organization(name=request.name.strip())
    db.add(organization)
    try:
        db.flush()
        record_user_action(
            db,
            actor=_,
            action="organization.created",
            object_type="organization",
            object_id=str(organization.id),
            after_data={"name": organization.name},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Organization đã tồn tại.",
        ) from exc
    db.refresh(organization)
    return {"id": organization.id, "name": organization.name}


@router.get("/factories")
def list_factories(
    organization_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
):
    query = select(Factory)
    if organization_id is not None:
        query = query.where(Factory.organization_id == organization_id)
    if user.role != "admin":
        accessible_factories = select(Plant.factory_id).where(
            Plant.id.in_(accessible_plant_ids(db, user) or [])
        )
        query = query.where(Factory.id.in_(accessible_factories))
    return [
        {
            "id": factory.id,
            "organization_id": factory.organization_id,
            "name": factory.name,
        }
        for factory in db.scalars(query.order_by(Factory.name)).all()
    ]


@router.post("/factories", status_code=status.HTTP_201_CREATED)
def create_factory(
    request: FactoryRequest,
    db: Session = Depends(get_db),
    _: AlertUser = Depends(require_roles("admin")),
):
    if db.get(Organization, request.organization_id) is None:
        raise HTTPException(status_code=404, detail="Organization không tồn tại.")
    factory = Factory(
        organization_id=request.organization_id,
        name=request.name.strip(),
    )
    db.add(factory)
    try:
        db.flush()
        record_user_action(
            db,
            actor=_,
            action="factory.created",
            object_type="factory",
            object_id=str(factory.id),
            after_data={
                "organization_id": factory.organization_id,
                "name": factory.name,
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Factory đã tồn tại trong Organization.",
        ) from exc
    db.refresh(factory)
    return {
        "id": factory.id,
        "organization_id": factory.organization_id,
        "name": factory.name,
    }


@router.get("/departments")
def list_departments(
    factory_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
):
    query = select(Department)
    if factory_id is not None:
        query = query.where(Department.factory_id == factory_id)
    if user.role != "admin":
        accessible_departments = select(Plant.department_id).where(
            Plant.id.in_(accessible_plant_ids(db, user) or [])
        )
        query = query.where(Department.id.in_(accessible_departments))
    return [
        {
            "id": department.id,
            "factory_id": department.factory_id,
            "name": department.name,
        }
        for department in db.scalars(query.order_by(Department.name)).all()
    ]


@router.post("/departments", status_code=status.HTTP_201_CREATED)
def create_department(
    request: DepartmentRequest,
    db: Session = Depends(get_db),
    _: AlertUser = Depends(require_roles("admin")),
):
    if db.get(Factory, request.factory_id) is None:
        raise HTTPException(status_code=404, detail="Factory không tồn tại.")
    department = Department(
        factory_id=request.factory_id,
        name=request.name.strip(),
    )
    db.add(department)
    try:
        db.flush()
        record_user_action(
            db,
            actor=_,
            action="department.created",
            object_type="department",
            object_id=str(department.id),
            after_data={
                "factory_id": department.factory_id,
                "name": department.name,
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Department đã tồn tại trong Factory.",
        ) from exc
    db.refresh(department)
    return {
        "id": department.id,
        "factory_id": department.factory_id,
        "name": department.name,
    }


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
            "factory_name": (
                db.get(Factory, plant.factory_id).name
                if plant.factory_id and db.get(Factory, plant.factory_id)
                else None
            ),
            "department_name": (
                db.get(Department, plant.department_id).name
                if plant.department_id and db.get(Department, plant.department_id)
                else None
            ),
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
    _validate_plant_organization_scope(
        db,
        request.factory_id,
        request.department_id,
    )
    plant = Plant(
        name=request.name.strip(),
        timezone=request.timezone.strip(),
        factory_id=request.factory_id,
        department_id=request.department_id,
    )
    db.add(plant)
    try:
        db.flush()
        record_user_action(
            db,
            actor=_,
            action="plant.created",
            object_type="monitoring_plant",
            object_id=str(plant.id),
            after_data={
                "name": plant.name,
                "factory_id": plant.factory_id,
                "department_id": plant.department_id,
            },
        )
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
    user: AlertUser = Depends(require_roles("admin")),
):
    plant = db.get(Plant, plant_id)
    if plant is None:
        raise HTTPException(status_code=404, detail="Nhà máy không tồn tại.")
    before_data = _plant_json(plant)
    if request.name is not None:
        plant.name = request.name.strip()
    if request.timezone is not None:
        plant.timezone = request.timezone.strip()
    factory_id = (
        request.factory_id
        if "factory_id" in request.model_fields_set
        else plant.factory_id
    )
    department_id = (
        request.department_id
        if "department_id" in request.model_fields_set
        else plant.department_id
    )
    if factory_id is None:
        raise HTTPException(
            status_code=422,
            detail="Plant phải được gán vào Factory để rời khỏi quarantine.",
        )
    _validate_plant_organization_scope(db, factory_id, department_id)
    plant.factory_id = factory_id
    plant.department_id = department_id
    record_user_action(
        db,
        actor=user,
        action="plant.updated",
        object_type="monitoring_plant",
        object_id=str(plant.id),
        before_data=before_data,
        after_data=_plant_json(plant),
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Tên nhà máy đã tồn tại.") from exc
    return {
        **_plant_json(plant),
        "factory_name": db.get(Factory, plant.factory_id).name,
        "department_name": (
            db.get(Department, plant.department_id).name
            if plant.department_id
            else None
        ),
    }


@router.delete("/plants/{plant_id}")
def delete_plant(
    plant_id: int,
    db: Session = Depends(get_db),
    _: AlertUser = Depends(require_roles("admin")),
):
    plant = db.get(Plant, plant_id)
    if plant is None:
        raise HTTPException(status_code=404, detail="Nhà máy không tồn tại.")
    record_user_action(
        db,
        actor=_,
        action="plant.deleted",
        object_type="monitoring_plant",
        object_id=str(plant.id),
        before_data=_plant_json(plant),
    )
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
        db.flush()
        record_user_action(
            db,
            actor=user,
            action="production_line.created",
            object_type="production_line",
            object_id=str(line.id),
            after_data={"plant_id": line.plant_id, "name": line.name},
        )
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
    previous_name = line.name
    line.name = request.name.strip()
    record_user_action(
        db,
        actor=user,
        action="production_line.updated",
        object_type="production_line",
        object_id=str(line.id),
        before_data={"name": previous_name},
        after_data={"name": line.name},
    )
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
    record_user_action(
        db,
        actor=user,
        action="production_line.deleted",
        object_type="production_line",
        object_id=str(line.id),
        before_data={"plant_id": line.plant_id, "name": line.name},
    )
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
    record_user_action(
        db,
        actor=user,
        action="monitoring_dataset.deleted",
        object_type="monitoring_dataset",
        object_id=str(dataset.id),
        before_data={
            "name": dataset.name,
            "plant_id": dataset.plant_id,
            "line_id": dataset.line_id,
            "enabled": dataset.enabled,
        },
    )
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
    record_user_action(
        db,
        actor=user,
        action="monitoring_dataset.created",
        object_type="monitoring_dataset",
        object_id=str(dataset.id),
        after_data={
            "name": dataset.name,
            "plant_id": dataset.plant_id,
            "line_id": dataset.line_id,
            "row_count": len(dataset.data_rows),
        },
    )
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
    record_user_action(
        db,
        actor=user,
        action="monitoring_dataset.uploaded",
        object_type="monitoring_dataset",
        object_id=str(dataset.id),
        after_data={
            "name": dataset.name,
            "plant_id": dataset.plant_id,
            "line_id": dataset.line_id,
            "row_count": len(rows),
            "files_merged": merged.report["files_merged"],
        },
    )
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
    before_data = {
        "schedule_interval": dataset.schedule_interval,
        "schedule_unit": dataset.schedule_unit,
        "enabled": dataset.enabled,
    }
    dataset.schedule_interval = request.interval
    dataset.schedule_unit = request.unit
    dataset.enabled = request.enabled
    record_user_action(
        db,
        actor=user,
        action="monitoring_dataset.schedule_updated",
        object_type="monitoring_dataset",
        object_id=str(dataset.id),
        before_data=before_data,
        after_data={
            "schedule_interval": dataset.schedule_interval,
            "schedule_unit": dataset.schedule_unit,
            "enabled": dataset.enabled,
        },
    )
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
    db.flush()
    record_user_action(
        db,
        actor=user,
        action="alert_rule.created",
        object_type="alert_rule",
        object_id=str(rule.id),
        after_data=request.model_dump(),
    )
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
    before_data = {
        key: getattr(rule, key)
        for key in request.model_dump()
    }
    for field, value in request.model_dump().items():
        setattr(rule, field, value)
    record_user_action(
        db,
        actor=user,
        action="alert_rule.updated",
        object_type="alert_rule",
        object_id=str(rule.id),
        before_data=before_data,
        after_data=request.model_dump(),
    )
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
    record_user_action(
        db,
        actor=user,
        action="alert_rule.deleted",
        object_type="alert_rule",
        object_id=str(rule.id),
        before_data={
            "name": rule.name,
            "plant_id": rule.plant_id,
            "metric": rule.metric,
            "threshold": rule.threshold,
            "enabled": rule.enabled,
        },
    )
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
    before_data = {
        "status": alert.status,
        "assigned_to": alert.assigned_to,
        "feedback": alert.feedback,
        "snoozed_until": (
            alert.snoozed_until.isoformat()
            if alert.snoozed_until is not None
            else None
        ),
    }

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
            require_plant_access(db, assignee, alert.plant_id, actor=user)
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
    after_data = {
        "status": alert.status,
        "assigned_to": alert.assigned_to,
        "feedback": alert.feedback,
        "snoozed_until": (
            alert.snoozed_until.isoformat()
            if alert.snoozed_until is not None
            else None
        ),
    }
    if before_data != after_data or request.note is not None or request.feedback_note is not None:
        record_user_action(
            db,
            actor=user,
            action="alert.updated",
            object_type="monitoring_alert",
            object_id=str(alert.id),
            before_data=before_data,
            after_data=after_data,
            detail=request.note or request.feedback_note,
        )
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
