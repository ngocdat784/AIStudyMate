import unittest
from datetime import datetime, timedelta, timezone
from typing import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models.monitoring import AlertUser, AuthSession


class AuthenticationSessionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)

        def override_get_db() -> Iterator[Session]:
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        self.old_secret = settings.ALERT_AUTH_SECRET
        self.old_bootstrap = settings.ALERT_BOOTSTRAP_TOKEN
        settings.ALERT_AUTH_SECRET = "test-auth-session-secret-32-characters"
        settings.ALERT_BOOTSTRAP_TOKEN = "test-auth-session-bootstrap"
        self.client = TestClient(app)
        response = self.client.post(
            "/api/monitoring/auth/bootstrap-admin",
            json={
                "email": "session-admin@example.test",
                "password": "secure-test-password",
                "display_name": "Session Admin",
                "bootstrap_token": "test-auth-session-bootstrap",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.user_id = response.json()["user"]["id"]
        self.token = response.json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.pop(get_db, None)
        settings.ALERT_AUTH_SECRET = self.old_secret
        settings.ALERT_BOOTSTRAP_TOKEN = self.old_bootstrap
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_sessions_can_be_listed_revoked_and_logged_out(self):
        second_login = self.client.post(
            "/api/monitoring/auth/login",
            json={
                "email": "session-admin@example.test",
                "password": "secure-test-password",
            },
        )
        self.assertEqual(second_login.status_code, 200, second_login.text)
        second_token = second_login.json()["access_token"]
        listed = self.client.get("/api/monitoring/auth/sessions", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 2)

        second_session = next(item for item in listed.json() if not item["current"])
        revoked = self.client.delete(
            f"/api/monitoring/auth/sessions/{second_session['id']}",
            headers=self.headers,
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(
            self.client.get(
                "/api/monitoring/auth/me",
                headers={"Authorization": f"Bearer {second_token}"},
            ).status_code,
            401,
        )
        logout = self.client.post("/api/monitoring/auth/logout", headers=self.headers)
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(
            self.client.get("/api/monitoring/auth/me", headers=self.headers).status_code,
            401,
        )

    def test_login_lockout_revokes_existing_sessions(self):
        for attempt in range(5):
            response = self.client.post(
                "/api/monitoring/auth/login",
                json={
                    "email": "session-admin@example.test",
                    "password": "incorrect-password",
                },
            )
        self.assertEqual(response.status_code, 423)
        self.assertEqual(
            self.client.get("/api/monitoring/auth/me", headers=self.headers).status_code,
            401,
        )
        with Session(self.engine) as session:
            user = session.get(AlertUser, self.user_id)
            self.assertIsNotNone(user)
            self.assertEqual(user.account_status, "locked")
            auth_session = session.scalar(
                select(AuthSession).where(AuthSession.user_id == self.user_id)
            )
            self.assertIsNotNone(auth_session)
            self.assertIsNotNone(auth_session.revoked_at)

    def test_password_change_revokes_sessions_and_allows_new_password(self):
        changed = self.client.post(
            "/api/monitoring/auth/change-password",
            headers=self.headers,
            json={
                "current_password": "secure-test-password",
                "new_password": "new-secure-test-password",
            },
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(
            self.client.get("/api/monitoring/auth/me", headers=self.headers).status_code,
            401,
        )
        login = self.client.post(
            "/api/monitoring/auth/login",
            json={
                "email": "session-admin@example.test",
                "password": "new-secure-test-password",
            },
        )
        self.assertEqual(login.status_code, 200, login.text)

    def test_invalid_tampered_and_expired_tokens_are_rejected(self):
        wrong_password = self.client.post(
            "/api/monitoring/auth/login",
            json={
                "email": "session-admin@example.test",
                "password": "wrong-secure-password",
            },
        )
        unknown_email = self.client.post(
            "/api/monitoring/auth/login",
            json={
                "email": "missing@example.test",
                "password": "wrong-secure-password",
            },
        )
        self.assertEqual(wrong_password.status_code, 401)
        self.assertEqual(unknown_email.status_code, 401)

        tampered_token = self.token[:-1] + (
            "A" if self.token[-1] != "A" else "B"
        )
        self.assertEqual(
            self.client.get(
                "/api/monitoring/auth/me",
                headers={"Authorization": f"Bearer {tampered_token}"},
            ).status_code,
            401,
        )

        with Session(self.engine) as session:
            auth_session = session.scalar(
                select(AuthSession).where(AuthSession.user_id == self.user_id)
            )
            self.assertIsNotNone(auth_session)
            auth_session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            session.commit()
        self.assertEqual(
            self.client.get("/api/monitoring/auth/me", headers=self.headers).status_code,
            401,
        )

    def test_bootstrap_is_one_time_and_deactivation_revokes_existing_token(self):
        self_escalation = self.client.post(
            "/api/monitoring/auth/signup",
            json={
                "email": "self-promoted@example.test",
                "password": "secure-test-password",
                "display_name": "Self Promoted",
                "role": "admin",
            },
        )
        self.assertEqual(self_escalation.status_code, 422)

        second_bootstrap = self.client.post(
            "/api/monitoring/auth/bootstrap-admin",
            json={
                "email": "second-admin@example.test",
                "password": "secure-test-password",
                "display_name": "Second Admin",
                "bootstrap_token": "test-auth-session-bootstrap",
            },
        )
        self.assertEqual(second_bootstrap.status_code, 409)

        created = self.client.post(
            "/api/monitoring/users",
            headers=self.headers,
            json={
                "email": "inactive-operator@example.test",
                "password": "secure-test-password",
                "display_name": "Inactive Operator",
                "role": "operator",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        user_id = created.json()["id"]
        login = self.client.post(
            "/api/monitoring/auth/login",
            json={
                "email": "inactive-operator@example.test",
                "password": "secure-test-password",
            },
        )
        self.assertEqual(login.status_code, 200, login.text)
        old_token = login.json()["access_token"]

        deactivated = self.client.patch(
            f"/api/monitoring/users/{user_id}",
            headers=self.headers,
            json={"role": "operator", "is_active": False},
        )
        self.assertEqual(deactivated.status_code, 200, deactivated.text)
        self.assertEqual(
            self.client.get(
                "/api/monitoring/auth/me",
                headers={"Authorization": f"Bearer {old_token}"},
            ).status_code,
            401,
        )
        inactive_login = self.client.post(
            "/api/monitoring/auth/login",
            json={
                "email": "inactive-operator@example.test",
                "password": "secure-test-password",
            },
        )
        self.assertEqual(inactive_login.status_code, 403)
