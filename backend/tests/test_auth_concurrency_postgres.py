from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import os
import secrets
import threading
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, or_, select, text
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
from app.services.monitoring_auth import hash_password, verify_password


POSTGRES_INTEGRATION_ENABLED = (
    os.environ.get("RUN_POSTGRES_INTEGRATION") == "1"
)


@unittest.skipUnless(
    POSTGRES_INTEGRATION_ENABLED,
    "Set RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL integration tests.",
)
class PostgresAuthenticationConcurrencyTests(unittest.TestCase):
    REQUEST_TIMEOUT_SECONDS = 25
    BARRIER_TIMEOUT_SECONDS = 8
    DATABASE_LOCK_TIMEOUT = "8s"

    def setUp(self):
        if engine.dialect.name != "postgresql":
            self.skipTest("Configured database is not PostgreSQL.")

        self.run_id = uuid.uuid4().hex
        self.user_ids: set[int] = set()
        self.auth_session_ids: set[int] = set()
        self.request_sessions: list[Session] = []
        self.request_sessions_lock = threading.Lock()
        self.old_auth_secret = settings.ALERT_AUTH_SECRET
        settings.ALERT_AUTH_SECRET = (
            "postgres-auth-concurrency-test-secret-32"
        )

        self.previous_get_db_override = app.dependency_overrides.get(get_db)
        self.had_get_db_override = get_db in app.dependency_overrides

        def override_get_db():
            with Session(engine) as db:
                with self.request_sessions_lock:
                    self.request_sessions.append(db)
                db.execute(
                    text(
                        f"SET LOCAL lock_timeout = "
                        f"'{self.DATABASE_LOCK_TIMEOUT}'"
                    )
                )
                yield db

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

        with Session(engine) as db:
            self.user_id, self.access_token, _ = self._create_user_and_session(
                db,
                "primary",
            )
            db.commit()

    def tearDown(self):
        if hasattr(self, "client"):
            self.client.close()

        if self.had_get_db_override:
            app.dependency_overrides[get_db] = self.previous_get_db_override
        else:
            app.dependency_overrides.pop(get_db, None)

        if self.user_ids:
            with Session(engine) as cleanup_session:
                audit_filters = [
                    UserActionAudit.actor_user_id.in_(self.user_ids),
                ]
                if self.auth_session_ids:
                    audit_filters.append(
                        (
                            UserActionAudit.object_type == "auth_session"
                        )
                        & UserActionAudit.object_id.in_(
                            [
                                str(session_id)
                                for session_id in self.auth_session_ids
                            ]
                        )
                    )
                cleanup_session.execute(
                    delete(UserActionAudit).where(or_(*audit_filters))
                )
                cleanup_session.execute(
                    delete(PasswordResetToken).where(
                        PasswordResetToken.user_id.in_(self.user_ids)
                    )
                )
                cleanup_session.execute(
                    delete(AuthSession).where(
                        AuthSession.id.in_(self.auth_session_ids)
                    )
                )
                cleanup_session.execute(
                    delete(AlertUser).where(AlertUser.id.in_(self.user_ids))
                )
                cleanup_session.commit()

        settings.ALERT_AUTH_SECRET = self.old_auth_secret

    def _create_user_and_session(self, db: Session, label: str):
        user = AlertUser(
            email=f"auth-concurrency-{label}-{self.run_id}@example.test",
            display_name=f"Concurrency Test {label}",
            password_hash=hash_password("initial-concurrency-password"),
            role="operator",
        )
        db.add(user)
        db.flush()
        self.user_ids.add(user.id)
        token, session_id = self._create_auth_session(db, user.id)
        return user.id, token, session_id

    def _create_auth_session(self, db: Session, user_id: int):
        token = secrets.token_urlsafe(48)
        auth_session = AuthSession(
            user_id=user_id,
            token_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(auth_session)
        db.flush()
        self.auth_session_ids.add(auth_session.id)
        return token, auth_session.id

    def _run_concurrently(self, requests):
        barrier = threading.Barrier(len(requests))

        def run_request(request):
            barrier.wait(timeout=self.BARRIER_TIMEOUT_SECONDS)
            return request()

        with ThreadPoolExecutor(max_workers=len(requests)) as executor:
            futures = [
                executor.submit(run_request, request)
                for request in requests
            ]
            responses = [
                future.result(timeout=self.REQUEST_TIMEOUT_SECONDS)
                for future in futures
            ]

        self.assertEqual(len(self.request_sessions), len(requests))
        self.assertEqual(
            len({id(db) for db in self.request_sessions}),
            len(requests),
            "Each concurrent request must use an independent DB session.",
        )
        return responses

    def _count_audits(self, *, action: str, object_id: str) -> int:
        with Session(engine) as db:
            return db.scalar(
                select(func.count(UserActionAudit.id)).where(
                    UserActionAudit.action == action,
                    UserActionAudit.object_id == object_id,
                )
            )

    def test_auth_c01_concurrent_failed_logins_reach_lockout_threshold(self):
        requests = [
            lambda: self.client.post(
                "/api/monitoring/auth/login",
                json={
                    "email": f"auth-concurrency-primary-{self.run_id}@example.test",
                    "password": "incorrect-concurrency-password",
                },
            )
            for _ in range(5)
        ]

        responses = self._run_concurrently(requests)

        self.assertEqual(
            sorted(response.status_code for response in responses),
            [401, 401, 401, 401, 423],
        )
        with Session(engine) as db:
            user = db.get(AlertUser, self.user_id)
            self.assertIsNotNone(user)
            self.assertEqual(user.failed_login_attempts, 5)
            self.assertEqual(user.account_status, "locked")
            self.assertIsNotNone(user.locked_until)
            self.assertGreater(
                user.locked_until,
                datetime.now(timezone.utc),
            )

    def test_auth_c02_concurrent_password_resets_use_token_once(self):
        raw_reset_token = secrets.token_urlsafe(48)
        reset_token = PasswordResetToken(
            user_id=self.user_id,
            token_hash=hashlib.sha256(
                raw_reset_token.encode("ascii")
            ).hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        with Session(engine) as db:
            db.add(reset_token)
            db.flush()
            reset_token_id = reset_token.id
            db.commit()

        new_passwords = (
            "concurrent-reset-password-one",
            "concurrent-reset-password-two",
        )
        responses = self._run_concurrently(
            [
                lambda password=password: self.client.post(
                    "/api/monitoring/auth/reset-password",
                    json={
                        "token": raw_reset_token,
                        "new_password": password,
                    },
                )
                for password in new_passwords
            ]
        )

        self.assertEqual(
            sorted(response.status_code for response in responses),
            [200, 400],
        )
        with Session(engine) as db:
            reset_token = db.get(PasswordResetToken, reset_token_id)
            user = db.get(AlertUser, self.user_id)
            self.assertIsNotNone(reset_token)
            self.assertIsNotNone(reset_token.used_at)
            self.assertIsNotNone(user)
            self.assertEqual(
                sum(
                    verify_password(password, user.password_hash)
                    for password in new_passwords
                ),
                1,
            )
        self.assertEqual(
            self._count_audits(
                action="account.password_reset",
                object_id=str(self.user_id),
            ),
            1,
        )

    def test_auth_c03_concurrent_session_revocation_creates_one_audit(self):
        with Session(engine) as db:
            _, target_session_id = self._create_auth_session(
                db,
                self.user_id,
            )
            db.commit()

        headers = {"Authorization": f"Bearer {self.access_token}"}
        endpoint = (
            f"/api/monitoring/auth/sessions/{target_session_id}"
        )
        responses = self._run_concurrently(
            [
                lambda: self.client.delete(endpoint, headers=headers),
                lambda: self.client.delete(endpoint, headers=headers),
            ]
        )

        self.assertEqual(
            [response.status_code for response in responses],
            [200, 200],
        )
        with Session(engine) as db:
            auth_session = db.get(AuthSession, target_session_id)
            self.assertIsNotNone(auth_session)
            self.assertIsNotNone(auth_session.revoked_at)
        self.assertEqual(
            self._count_audits(
                action="account.session_revoked",
                object_id=str(target_session_id),
            ),
            1,
        )

    def test_auth_c04_user_cannot_revoke_another_users_session(self):
        with Session(engine) as db:
            other_user_id, _, other_session_id = (
                self._create_user_and_session(db, "other")
            )
            db.commit()

        response = self.client.delete(
            f"/api/monitoring/auth/sessions/{other_session_id}",
            headers={"Authorization": f"Bearer {self.access_token}"},
        )

        self.assertEqual(response.status_code, 404, response.text)
        with Session(engine) as db:
            other_session = db.get(AuthSession, other_session_id)
            self.assertIsNotNone(other_session)
            self.assertEqual(other_session.user_id, other_user_id)
            self.assertIsNone(other_session.revoked_at)
        self.assertEqual(
            self._count_audits(
                action="account.session_revoked",
                object_id=str(other_session_id),
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
