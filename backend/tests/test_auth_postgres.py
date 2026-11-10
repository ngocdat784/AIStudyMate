
from __future__ import annotations

import hashlib
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database.connection import engine, get_db
from app.main import app
from app.models.monitoring import (
    AlertUser,
    AuthSession,
    PasswordResetToken,
    UserActionAudit,
)
from app.routes import monitoring as monitoring_routes
from app.services.monitoring_auth import verify_password


POSTGRES_INTEGRATION_ENABLED = (
    os.environ.get("RUN_POSTGRES_INTEGRATION") == "1"
)


@unittest.skipUnless(
    POSTGRES_INTEGRATION_ENABLED,
    "Set RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL integration tests.",
)
class PostgresAuthenticationIntegrationTests(unittest.TestCase):

    def setUp(self):
        if engine.dialect.name != "postgresql":
            self.skipTest("Configured database is not PostgreSQL.")

        self.run_id = uuid.uuid4().hex
        self.email = f"auth-{self.run_id}@example.test"
        self.old_secret = settings.ALERT_AUTH_SECRET
        self.old_bootstrap = settings.ALERT_BOOTSTRAP_TOKEN

        settings.ALERT_AUTH_SECRET = (
            "postgres-auth-integration-test-secret-32"
        )
        settings.ALERT_BOOTSTRAP_TOKEN = (
            "postgres-auth-integration-bootstrap"
        )

        def override_get_db():
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        if hasattr(self, "client"):
            self.client.close()

        app.dependency_overrides.pop(get_db, None)

        # Only clean records belonging to this test run.
        with Session(engine) as session:
            users = list(
                session.scalars(
                    select(AlertUser).where(
                        AlertUser.email.like(f"auth-{self.run_id}%")
                    )
                )
            )
            user_ids = [user.id for user in users]

            if user_ids:
                session.execute(
                    delete(UserActionAudit).where(
                        or_(
                            UserActionAudit.actor_user_id.in_(user_ids),
                            (
                                (UserActionAudit.object_type == "alert_user")
                                & UserActionAudit.object_id.in_(
                                    [str(user_id) for user_id in user_ids]
                                )
                            ),
                        )
                    )
                )

                session.execute(
                    delete(AlertUser).where(
                        AlertUser.id.in_(user_ids)
                    )
                )

            session.commit()

        settings.ALERT_AUTH_SECRET = self.old_secret
        settings.ALERT_BOOTSTRAP_TOKEN = self.old_bootstrap

    def _signup(self):
        response = self.client.post(
            "/api/monitoring/auth/signup",
            json={
                "email": self.email,
                "password": "initial-secure-password",
                "display_name": "PostgreSQL Auth Test",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_signup_persists_user_password_hash_and_session(self):
        result = self._signup()

        user_id = result["user"]["id"]
        raw_token = result["access_token"]

        self.assertEqual(result["token_type"], "bearer")
        self.assertEqual(result["user"]["role"], "viewer")

        with Session(engine) as session:
            user = session.get(AlertUser, user_id)

            self.assertIsNotNone(user)
            self.assertEqual(user.email, self.email)
            self.assertEqual(user.role, "viewer")
            self.assertTrue(user.is_active)
            self.assertEqual(user.account_status, "active")

            self.assertNotEqual(
                user.password_hash,
                "initial-secure-password",
            )
            self.assertTrue(
                verify_password(
                    "initial-secure-password",
                    user.password_hash,
                )
            )

            auth_session = session.scalar(
                select(AuthSession).where(
                    AuthSession.user_id == user_id
                )
            )

            self.assertIsNotNone(auth_session)
            self.assertEqual(
                auth_session.token_hash,
                hashlib.sha256(raw_token.encode("ascii")).hexdigest(),
            )
            self.assertNotEqual(auth_session.token_hash, raw_token)
            self.assertIsNone(auth_session.revoked_at)
            self.assertGreater(
                auth_session.expires_at,
                datetime.now(timezone.utc),
            )

        me = self.client.get(
            "/api/monitoring/auth/me",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual(me.json()["id"], user_id)

    def test_signup_rejects_duplicate_email(self):
        self._signup()

        duplicate = self.client.post(
            "/api/monitoring/auth/signup",
            json={
                "email": self.email,
                "password": "another-secure-password",
                "display_name": "Duplicate Account",
            },
        )

        self.assertEqual(duplicate.status_code, 409)

        with Session(engine) as session:
            users = list(
                session.scalars(
                    select(AlertUser).where(
                        AlertUser.email == self.email
                    )
                )
            )
            self.assertEqual(len(users), 1)

    def test_forgot_password_does_not_disclose_account_existence(self):
        self._signup()

        captured_tokens: list[str] = []

        with (
            patch.object(
                monitoring_routes,
                "notification_channel_status",
                return_value={"email": True},
            ),
            patch.object(
                monitoring_routes,
                "send_password_reset_email",
                side_effect=lambda email, token: captured_tokens.append(token),
            ) as send_email,
        ):
            existing = self.client.post(
                "/api/monitoring/auth/forgot-password",
                json={"email": self.email},
            )

            missing = self.client.post(
                "/api/monitoring/auth/forgot-password",
                json={"email": f"missing-{self.run_id}@example.test"},
            )

        self.assertEqual(existing.status_code, 202, existing.text)
        self.assertEqual(missing.status_code, 202, missing.text)
        self.assertEqual(existing.json(), missing.json())

        send_email.assert_called_once()
        self.assertEqual(len(captured_tokens), 1)

        raw_token = captured_tokens[0]
        expected_hash = hashlib.sha256(
            raw_token.encode("ascii")
        ).hexdigest()

        with Session(engine) as session:
            reset_token = session.scalar(
                select(PasswordResetToken).where(
                    PasswordResetToken.token_hash == expected_hash
                )
            )

            self.assertIsNotNone(reset_token)
            self.assertNotEqual(reset_token.token_hash, raw_token)
            self.assertIsNone(reset_token.used_at)
            self.assertGreater(
                reset_token.expires_at,
                datetime.now(timezone.utc),
            )

    def test_password_reset_is_single_use_revokes_sessions_and_audits(self):
        signup = self._signup()
        user_id = signup["user"]["id"]
        original_token = signup["access_token"]

        second_login = self.client.post(
            "/api/monitoring/auth/login",
            json={
                "email": self.email,
                "password": "initial-secure-password",
            },
        )
        self.assertEqual(second_login.status_code, 200, second_login.text)
        second_token = second_login.json()["access_token"]

        captured_tokens: list[str] = []

        with (
            patch.object(
                monitoring_routes,
                "notification_channel_status",
                return_value={"email": True},
            ),
            patch.object(
                monitoring_routes,
                "send_password_reset_email",
                side_effect=lambda email, token: captured_tokens.append(token),
            ),
        ):
            forgot = self.client.post(
                "/api/monitoring/auth/forgot-password",
                json={"email": self.email},
            )

        self.assertEqual(forgot.status_code, 202, forgot.text)
        self.assertEqual(len(captured_tokens), 1)

        raw_reset_token = captured_tokens[0]

        reset = self.client.post(
            "/api/monitoring/auth/reset-password",
            json={
                "token": raw_reset_token,
                "new_password": "replacement-secure-password",
            },
        )

        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertEqual(
            reset.json(),
            {
                "password_reset": True,
                "sessions_revoked": True,
            },
        )

        # The reset token must not be reusable.
        reused = self.client.post(
            "/api/monitoring/auth/reset-password",
            json={
                "token": raw_reset_token,
                "new_password": "another-replacement-password",
            },
        )
        self.assertEqual(reused.status_code, 400)

        # Both sessions created before password reset must be revoked.
        for old_token in (original_token, second_token):
            response = self.client.get(
                "/api/monitoring/auth/me",
                headers={"Authorization": f"Bearer {old_token}"},
            )
            self.assertEqual(response.status_code, 401)

        with Session(engine) as session:
            user = session.get(AlertUser, user_id)
            self.assertIsNotNone(user)
            self.assertTrue(
                verify_password(
                    "replacement-secure-password",
                    user.password_hash,
                )
            )
            self.assertFalse(
                verify_password(
                    "initial-secure-password",
                    user.password_hash,
                )
            )
            self.assertEqual(user.failed_login_attempts, 0)
            self.assertEqual(user.account_status, "active")
            self.assertIsNone(user.locked_until)

            reset_record = session.scalar(
                select(PasswordResetToken).where(
                    PasswordResetToken.token_hash
                    == hashlib.sha256(
                        raw_reset_token.encode("ascii")
                    ).hexdigest()
                )
            )
            self.assertIsNotNone(reset_record)
            self.assertIsNotNone(reset_record.used_at)

            sessions = list(
                session.scalars(
                    select(AuthSession).where(
                        AuthSession.user_id == user_id
                    )
                )
            )
            self.assertGreaterEqual(len(sessions), 2)
            self.assertTrue(
                all(item.revoked_at is not None for item in sessions)
            )

            audit = session.scalar(
                select(UserActionAudit).where(
                    UserActionAudit.actor_user_id == user_id,
                    UserActionAudit.action == "account.password_reset",
                    UserActionAudit.object_id == str(user_id),
                )
            )
            self.assertIsNotNone(audit)

        # The new password must work and create a new session.
        new_login = self.client.post(
            "/api/monitoring/auth/login",
            json={
                "email": self.email,
                "password": "replacement-secure-password",
            },
        )
        self.assertEqual(new_login.status_code, 200, new_login.text)

    def test_expired_password_reset_token_is_rejected(self):
        signup = self._signup()
        user_id = signup["user"]["id"]

        raw_token = f"expired-{uuid.uuid4().hex}-reset-token"
        token_hash = hashlib.sha256(
            raw_token.encode("ascii")
        ).hexdigest()

        with Session(engine) as session:
            session.add(
                PasswordResetToken(
                    user_id=user_id,
                    token_hash=token_hash,
                    expires_at=(
                        datetime.now(timezone.utc) - timedelta(minutes=1)
                    ),
                )
            )
            session.commit()

        response = self.client.post(
            "/api/monitoring/auth/reset-password",
            json={
                "token": raw_token,
                "new_password": "replacement-secure-password",
            },
        )

        self.assertEqual(response.status_code, 400)

        with Session(engine) as session:
            user = session.get(AlertUser, user_id)
            self.assertIsNotNone(user)
            self.assertTrue(
                verify_password(
                    "initial-secure-password",
                    user.password_hash,
                )
            )


if __name__ == "__main__":
    unittest.main()

