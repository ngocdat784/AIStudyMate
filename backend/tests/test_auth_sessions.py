import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from typing import Iterator
from unittest.mock import patch

import bcrypt

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models.monitoring import AlertUser, AuthSession, PasswordResetToken
from app.services.monitoring_auth import hash_password, verify_password


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

    def test_current_user_uses_swagger_bearer_authentication(self):
        openapi = self.client.get("/openapi.json").json()

        self.assertIn("HTTPBearer", openapi["components"]["securitySchemes"])
        self.assertEqual(
            openapi["components"]["securitySchemes"]["HTTPBearer"]["scheme"],
            "bearer",
        )
        self.assertIn(
            {"HTTPBearer": []},
            openapi["paths"]["/api/monitoring/auth/me"]["get"]["security"],
        )

        unauthorized = self.client.get("/api/monitoring/auth/me")
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(unauthorized.json()["detail"], "Yêu cầu đăng nhập.")

        wrong_scheme = self.client.get(
            "/api/monitoring/auth/me",
            headers={"Authorization": "Basic not-a-bearer-token"},
        )
        self.assertEqual(wrong_scheme.status_code, 401)
        self.assertEqual(wrong_scheme.json()["detail"], "Yêu cầu đăng nhập.")

        authenticated = self.client.get(
            "/api/monitoring/auth/me",
            headers=self.headers,
        )
        self.assertEqual(authenticated.status_code, 200)

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

    def test_existing_admin_login_generates_token_without_valid_dev_secret(self):
        for configured_secret in (None, "short"):
            with (
                self.subTest(configured_secret=configured_secret),
                patch.object(settings, "APP_ENV", "development"),
                patch.object(settings, "ALERT_AUTH_SECRET", configured_secret),
            ):
                login = self.client.post(
                    "/api/monitoring/auth/login",
                    json={
                        "email": "session-admin@example.test",
                        "password": "secure-test-password",
                    },
                )
                self.assertEqual(login.status_code, 200, login.text)
                self.assertEqual(login.json()["user"]["role"], "admin")
                self.assertGreaterEqual(len(settings.ALERT_AUTH_SECRET or ""), 32)

                current_user = self.client.get(
                    "/api/monitoring/auth/me",
                    headers={
                        "Authorization": f"Bearer {login.json()['access_token']}"
                    },
                )
                self.assertEqual(current_user.status_code, 200, current_user.text)
                self.assertEqual(current_user.json()["role"], "admin")

    def test_bcrypt_password_is_rehashed_after_successful_login(self):
        legacy_hash = bcrypt.hashpw(
            b"secure-test-password",
            bcrypt.gensalt(),
        ).decode("ascii")
        with Session(self.engine) as session:
            user = session.get(AlertUser, self.user_id)
            self.assertIsNotNone(user)
            user.password_hash = legacy_hash
            session.commit()

        rejected = self.client.post(
            "/api/monitoring/auth/login",
            json={
                "email": "session-admin@example.test",
                "password": "incorrect-secure-password",
            },
        )
        self.assertEqual(rejected.status_code, 401)
        with Session(self.engine) as session:
            user = session.get(AlertUser, self.user_id)
            self.assertEqual(user.password_hash, legacy_hash)

        accepted = self.client.post(
            "/api/monitoring/auth/login",
            json={
                "email": "session-admin@example.test",
                "password": "secure-test-password",
            },
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        with Session(self.engine) as session:
            user = session.get(AlertUser, self.user_id)
            self.assertTrue(user.password_hash.startswith("scrypt$"))
            self.assertTrue(
                verify_password(
                    "secure-test-password",
                    user.password_hash,
                )
            )

    def test_short_existing_password_requires_one_time_upgrade_before_login(self):
        short_password = "short-pass"
        with Session(self.engine) as session:
            user = session.get(AlertUser, self.user_id)
            self.assertIsNotNone(user)
            user.password_hash = hash_password(short_password)
            session.commit()

        rejected = self.client.post(
            "/api/monitoring/auth/login",
            json={
                "email": "session-admin@example.test",
                "password": "wrong-short",
            },
        )
        self.assertEqual(rejected.status_code, 401)

        required = self.client.post(
            "/api/monitoring/auth/login",
            json={
                "email": "session-admin@example.test",
                "password": short_password,
            },
        )
        self.assertEqual(required.status_code, 200, required.text)
        payload = required.json()
        self.assertTrue(payload["password_change_required"])
        self.assertNotIn("access_token", payload)
        self.assertEqual(
            self.client.get("/api/monitoring/auth/me", headers=self.headers).status_code,
            401,
        )

        too_short = self.client.post(
            "/api/monitoring/auth/reset-password",
            json={"token": payload["password_reset_token"], "new_password": "still-short"},
        )
        self.assertEqual(too_short.status_code, 422)

        upgraded = self.client.post(
            "/api/monitoring/auth/reset-password",
            json={
                "token": payload["password_reset_token"],
                "new_password": "new-secure-password",
            },
        )
        self.assertEqual(upgraded.status_code, 200, upgraded.text)
        replayed = self.client.post(
            "/api/monitoring/auth/reset-password",
            json={
                "token": payload["password_reset_token"],
                "new_password": "another-secure-password",
            },
        )
        self.assertEqual(replayed.status_code, 400)

        login = self.client.post(
            "/api/monitoring/auth/login",
            json={
                "email": "session-admin@example.test",
                "password": "new-secure-password",
            },
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.assertIn("access_token", login.json())
        self.assertNotIn("password_change_required", login.json())

        with Session(self.engine) as session:
            reset_token = session.scalar(
                select(PasswordResetToken).where(
                    PasswordResetToken.user_id == self.user_id
                )
            )
            self.assertIsNotNone(reset_token)
            self.assertIsNotNone(reset_token.used_at)
            self.assertEqual(
                reset_token.token_hash,
                hashlib.sha256(payload["password_reset_token"].encode("ascii")).hexdigest(),
            )
            self.assertNotEqual(reset_token.token_hash, payload["password_reset_token"])

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
