from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import case, delete
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.config import settings
from app.database.connection import get_db
from app.models.api_rate_limit import ApiRateLimitBucket
from app.services.monitoring_auth import ensure_auth_configured


def rate_limit_dependency(
    name: str,
    *,
    limit: int,
    window_seconds: int,
) -> Callable:
    if limit < 1 or window_seconds < 1:
        raise ValueError("Rate-limit values must be positive.")

    def enforce_rate_limit(
        request: Request,
        db: Session = Depends(get_db),
    ) -> None:
        if not settings.SECURITY_RATE_LIMITS_ENABLED:
            return

        secret = settings.ALERT_AUTH_SECRET
        if not secret or len(secret) < 32:
            ensure_auth_configured()
            raise RuntimeError("The configured rate-limit secret is invalid.")
        client_host = request.client.host if request.client else "unknown"
        material = f"api-rate-limit:v1:{name}:{client_host}".encode("utf-8")
        bucket_key = hmac.new(
            secret.encode("utf-8"),
            material,
            hashlib.sha256,
        ).hexdigest()

        now_epoch = int(time.time())
        window_start = now_epoch - now_epoch % window_seconds
        expires_at = datetime.fromtimestamp(
            window_start + window_seconds * 2,
            tz=timezone.utc,
        )
        values = {
            "bucket_key": bucket_key,
            "window_start": window_start,
            "request_count": 1,
            "expires_at": expires_at,
        }
        dialect_name = db.get_bind().dialect.name
        if dialect_name == "postgresql":
            insert = postgresql_insert(ApiRateLimitBucket).values(**values)
        elif dialect_name == "sqlite":
            insert = sqlite_insert(ApiRateLimitBucket).values(**values)
        else:
            raise RuntimeError(
                f"Rate limiting is not configured for database dialect {dialect_name!r}."
            )

        statement = insert.on_conflict_do_update(
            index_elements=[ApiRateLimitBucket.bucket_key],
            set_={
                "window_start": insert.excluded.window_start,
                "request_count": case(
                    (
                        ApiRateLimitBucket.window_start
                        == insert.excluded.window_start,
                        ApiRateLimitBucket.request_count + 1,
                    ),
                    else_=1,
                ),
                "expires_at": insert.excluded.expires_at,
            }
        ).returning(ApiRateLimitBucket.request_count)
        count = db.scalar(statement)

        if now_epoch % 60 == 0:
            db.execute(
                delete(ApiRateLimitBucket).where(
                    ApiRateLimitBucket.expires_at
                    < datetime.now(timezone.utc)
                )
            )
        db.commit()
        if count is not None and count > limit:
            retry_after = max(
                1,
                window_start + window_seconds - now_epoch,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Quá nhiều yêu cầu. Vui lòng thử lại sau.",
                headers={"Retry-After": str(retry_after)},
            )

    return enforce_rate_limit
