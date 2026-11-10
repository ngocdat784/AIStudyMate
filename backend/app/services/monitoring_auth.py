import base64
import hashlib
import hmac
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database.connection import get_db
from app.models.monitoring import (
    AlertUser,
    AlertUserDepartment,
    AlertUserFactory,
    AlertUserPlant,
    AuthSession,
    PasswordResetToken,
    Plant,
)
from app.services.user_audit import record_user_action


TOKEN_TTL_SECONDS = 60 * 60 * 12
PASSWORD_RESET_TTL_SECONDS = 60 * 30
LOGIN_FAILURE_LIMIT = 5
LOGIN_LOCK_SECONDS = 15 * 60


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _secret() -> bytes:
    secret = settings.ALERT_AUTH_SECRET
    if not secret or len(secret) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cần cấu hình ALERT_AUTH_SECRET tối thiểu 32 ký tự để bật xác thực.",
        )
    return secret.encode("utf-8")


def ensure_auth_configured() -> None:
    _secret()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32
    )
    return f"scrypt${_b64(salt)}${_b64(derived)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_text, expected_text = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected = base64.urlsafe_b64decode(
            expected_text + "=" * (-len(expected_text) % 4)
        )
    except (ValueError, TypeError):
        return False
    actual = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32
    )
    return hmac.compare_digest(actual, expected)


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def create_access_token(
    db: Session,
    user: AlertUser,
    request: Request | None = None,
) -> str:
    ensure_auth_configured()
    token = secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    session = AuthSession(
        user_id=user.id,
        token_hash=_token_digest(token),
        expires_at=now + timedelta(seconds=TOKEN_TTL_SECONDS),
        user_agent=(
            request.headers.get("user-agent", "")[:500]
            if request is not None
            else None
        ),
        last_seen_at=now,
    )
    db.add(session)
    return token


def revoke_user_sessions(db: Session, user_id: int) -> None:
    now = datetime.now(timezone.utc)
    sessions = db.scalars(
        select(AuthSession)
        .where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
        .order_by(AuthSession.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    for session in sessions:
        session.revoked_at = now


def revoke_access_token(db: Session, token: str) -> bool:
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == _token_digest(token),
            AuthSession.revoked_at.is_(None),
        )
    )
    if session is None:
        return False
    session.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return True


def _invalid_token() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token không hợp lệ, đã thu hồi hoặc hết hạn.",
    )


def _get_session_user(db: Session, token: str) -> tuple[AuthSession, AlertUser]:
    ensure_auth_configured()
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == _token_digest(token),
            AuthSession.revoked_at.is_(None),
        )
    )
    if session is None:
        raise _invalid_token()
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise _invalid_token()
    user = db.get(AlertUser, session.user_id)
    if (
        user is None
        or not user.is_active
        or user.account_status != "active"
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tài khoản không tồn tại, đã bị vô hiệu hóa hoặc khóa.",
        )
    return session, user


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> AlertUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Yêu cầu đăng nhập.",
        )
    _, user = _get_session_user(db, authorization[7:])
    return user


def get_current_session(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> tuple[AuthSession, AlertUser]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Yêu cầu đăng nhập.",
        )
    return _get_session_user(db, authorization[7:])


def require_roles(*roles: str):
    def dependency(
        request: Request,
        user: AlertUser = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> AlertUser:
        if user.role not in roles:
            record_user_action(
                db,
                actor=user,
                action="access.denied",
                object_type="api_route",
                object_id=request.url.path,
                outcome="denied",
                detail="role_not_permitted",
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn không có quyền thực hiện thao tác này.",
            )
        return user

    return dependency


def has_plant_access(
    db: Session,
    user: AlertUser,
    plant_id: int,
) -> bool:
    if user.role == "admin":
        return True
    plant = db.get(Plant, plant_id)
    if plant is None or plant.factory_id is None:
        return False
    if db.scalar(
        select(AlertUserPlant.id).where(
            AlertUserPlant.user_id == user.id,
            AlertUserPlant.plant_id == plant_id,
        )
    ) is None:
        return False
    if db.scalar(
        select(AlertUserFactory.id).where(
            AlertUserFactory.user_id == user.id,
            AlertUserFactory.factory_id == plant.factory_id,
        )
    ) is None:
        return False
    return (
        plant.department_id is None
        or db.scalar(
            select(AlertUserDepartment.id).where(
                AlertUserDepartment.user_id == user.id,
                AlertUserDepartment.department_id == plant.department_id,
            )
        )
        is not None
    )


def accessible_plant_ids(db: Session, user: AlertUser) -> list[int] | None:
    if user.role == "admin":
        return None
    return list(
        db.scalars(
            select(Plant.id)
            .join(
                AlertUserPlant,
                AlertUserPlant.plant_id == Plant.id,
            )
            .where(
                AlertUserPlant.user_id == user.id,
                Plant.factory_id.is_not(None),
                Plant.factory_id.in_(
                    select(AlertUserFactory.factory_id).where(
                        AlertUserFactory.user_id == user.id
                    )
                ),
                or_(
                    Plant.department_id.is_(None),
                    Plant.department_id.in_(
                        select(AlertUserDepartment.department_id).where(
                            AlertUserDepartment.user_id == user.id
                        )
                    ),
                ),
            )
        ).all()
    )


def require_plant_access(
    db: Session,
    user: AlertUser,
    plant_id: int,
    *,
    actor: AlertUser | None = None,
) -> None:
    if not has_plant_access(db, user, plant_id):
        record_user_action(
            db,
            actor=actor or user,
            action="access.denied",
            object_type="plant",
            object_id=str(plant_id),
            outcome="denied",
            detail="plant_scope_not_permitted",
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không được cấp quyền truy cập nhà máy này.",
        )


def optional_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> AlertUser | None:
    if not authorization:
        return None
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header không hợp lệ.",
        )
    _, user = _get_session_user(db, authorization[7:])
    return user


def send_password_reset_email(
    email: str,
    token: str,
) -> None:
    if not settings.ALERT_SMTP_HOST or not settings.ALERT_SMTP_FROM:
        raise RuntimeError("SMTP password-reset delivery is not configured.")
    if not settings.ALERT_SMTP_USE_TLS and settings.ALERT_SMTP_PORT != 465:
        raise RuntimeError("SMTP TLS is required.")
    reset_url = (
        f"{settings.APP_BASE_URL.rstrip('/')}/reset-password"
        f"?token={token}"
    )
    message = EmailMessage()
    message["Subject"] = "Đặt lại mật khẩu"
    message["From"] = settings.ALERT_SMTP_FROM
    message["To"] = email
    message.set_content(
        "Yêu cầu đặt lại mật khẩu đã được nhận. "
        f"Liên kết có hiệu lực 30 phút: {reset_url}"
    )
    context = ssl.create_default_context()
    if settings.ALERT_SMTP_PORT == 465:
        with smtplib.SMTP_SSL(
            settings.ALERT_SMTP_HOST,
            settings.ALERT_SMTP_PORT,
            timeout=8,
            context=context,
        ) as client:
            if settings.ALERT_SMTP_USERNAME:
                client.login(
                    settings.ALERT_SMTP_USERNAME,
                    settings.ALERT_SMTP_PASSWORD or "",
                )
            client.send_message(message)
    else:
        with smtplib.SMTP(
            settings.ALERT_SMTP_HOST,
            settings.ALERT_SMTP_PORT,
            timeout=8,
        ) as client:
            client.starttls(context=context)
            if settings.ALERT_SMTP_USERNAME:
                client.login(
                    settings.ALERT_SMTP_USERNAME,
                    settings.ALERT_SMTP_PASSWORD or "",
                )
            client.send_message(message)
