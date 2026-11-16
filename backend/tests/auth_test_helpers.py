from typing import Iterator

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.main import app
from app.config import settings
from app.database.base import Base
from app.database.connection import get_db
from app.models.monitoring import AlertUser
from app.services.monitoring_auth import get_current_user

_TEST_RATE_LIMIT_SECRET = "test-rate-limit-secret-for-route-tests"
_SET_TEST_SECRET = False
_TEST_ENGINE = None


def override_admin_auth() -> None:
    global _SET_TEST_SECRET, _TEST_ENGINE
    if settings.ALERT_AUTH_SECRET is None:
        settings.ALERT_AUTH_SECRET = _TEST_RATE_LIMIT_SECRET
        _SET_TEST_SECRET = True
    if get_db not in app.dependency_overrides:
        _TEST_ENGINE = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(_TEST_ENGINE)

        def override_get_db() -> Iterator[Session]:
            with Session(_TEST_ENGINE) as db:
                yield db

        app.dependency_overrides[get_db] = override_get_db
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
    global _SET_TEST_SECRET, _TEST_ENGINE
    app.dependency_overrides.pop(get_current_user, None)
    if _SET_TEST_SECRET:
        settings.ALERT_AUTH_SECRET = None
        _SET_TEST_SECRET = False
    if _TEST_ENGINE is not None:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(_TEST_ENGINE)
        _TEST_ENGINE.dispose()
        _TEST_ENGINE = None


def store_test_merged_hardware_dataset(dataframe: pd.DataFrame) -> str:
    if _TEST_ENGINE is None:
        raise RuntimeError("Call override_admin_auth() before storing test data.")

    from app.services.multi_file_service import store_merged_hardware_dataset

    with Session(_TEST_ENGINE) as db:
        return store_merged_hardware_dataset(
            dataframe,
            owner_user_id=1,
            db=db,
        )
