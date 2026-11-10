from app.main import app
from app.models.monitoring import AlertUser
from app.services.monitoring_auth import get_current_user


def override_admin_auth() -> None:
    app.dependency_overrides[get_current_user] = lambda: AlertUser(
        id=1,
        email="test-admin@example.test",
        display_name="Test Admin",
        password_hash="test-only",
        role="admin",
        is_active=True,
        account_status="active",
    )


def clear_admin_auth_override() -> None:
    app.dependency_overrides.pop(get_current_user, None)
