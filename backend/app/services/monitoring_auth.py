import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database.connection import get_db
from app.models.monitoring import AlertUser, AlertUserPlant


TOKEN_TTL_SECONDS = 60 * 60 * 12


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


def create_access_token(user: AlertUser) -> str:
    payload = _b64(
        json.dumps(
            {"sub": user.id, "role": user.role, "exp": int(time.time()) + TOKEN_TTL_SECONDS},
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signature = _b64(hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def _decode_token(token: str) -> dict[str, Any]:
    try:
        payload, signature = token.split(".", 1)
        expected = hmac.new(
            _secret(), payload.encode("ascii"), hashlib.sha256
        ).digest()
        actual = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        if not hmac.compare_digest(expected, actual):
            raise ValueError("invalid signature")
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(raw)
        if int(claims["exp"]) <= int(time.time()):
            raise ValueError("expired token")
        return claims
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token không hợp lệ hoặc đã hết hạn.",
        ) from exc


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> AlertUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Yêu cầu đăng nhập.",
        )
    claims = _decode_token(authorization[7:])
    user = db.get(AlertUser, int(claims["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tài khoản không tồn tại hoặc đã bị vô hiệu hóa.",
        )
    return user


def require_roles(*roles: str):
    def dependency(user: AlertUser = Depends(get_current_user)) -> AlertUser:
        if user.role not in roles:
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
    return (
        db.scalar(
            select(AlertUserPlant.id).where(
                AlertUserPlant.user_id == user.id,
                AlertUserPlant.plant_id == plant_id,
            )
        )
        is not None
    )


def accessible_plant_ids(db: Session, user: AlertUser) -> list[int] | None:
    if user.role == "admin":
        return None
    return list(
        db.scalars(
            select(AlertUserPlant.plant_id).where(
                AlertUserPlant.user_id == user.id
            )
        ).all()
    )


def require_plant_access(
    db: Session,
    user: AlertUser,
    plant_id: int,
) -> None:
    if not has_plant_access(db, user, plant_id):
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
    claims = _decode_token(authorization[7:])
    user = db.get(AlertUser, int(claims["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tài khoản không tồn tại hoặc đã bị vô hiệu hóa.",
        )
    return user
