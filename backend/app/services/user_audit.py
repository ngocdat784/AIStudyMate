from typing import Any

from sqlalchemy.orm import Session

from app.models.monitoring import AlertUser, UserActionAudit


def record_user_action(
    db: Session,
    *,
    actor: AlertUser | None,
    action: str,
    object_type: str,
    object_id: str | None,
    before_data: dict[str, Any] | None = None,
    after_data: dict[str, Any] | None = None,
    outcome: str = "success",
    detail: str | None = None,
) -> None:
    db.add(
        UserActionAudit(
            actor_user_id=actor.id if actor else None,
            action=action,
            object_type=object_type,
            object_id=object_id,
            source="api",
            outcome=outcome,
            before_data=before_data,
            after_data=after_data,
            detail=detail,
        )
    )
