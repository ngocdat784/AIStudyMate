import unittest
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

from app.config import settings
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models import AnalysisHistory, HardwareAnalysisJob
from app.models.monitoring import AlertUser, Plant, UserActionAudit
from app.services import hardware_analysis_jobs
from app.services.hardware_analysis_jobs import (
    _claim_next_job,
    fail_hardware_analysis_job,
    start_hardware_analysis_workers,
    stop_hardware_analysis_workers,
)
from app.services.monitoring_auth import create_access_token, hash_password


class HardwareAnalysisJobTests(unittest.TestCase):
    def setUp(self):
        self.saved_settings = {
            "ALERT_AUTH_SECRET": settings.ALERT_AUTH_SECRET,
            "ALERT_BOOTSTRAP_TOKEN": settings.ALERT_BOOTSTRAP_TOKEN,
            "ALERT_SCHEDULER_ENABLED": settings.ALERT_SCHEDULER_ENABLED,
            "HARDWARE_ANALYSIS_JOBS_ENABLED": (
                settings.HARDWARE_ANALYSIS_JOBS_ENABLED
            ),
            "HARDWARE_ANALYSIS_JOB_MAX_PENDING": (
                settings.HARDWARE_ANALYSIS_JOB_MAX_PENDING
            ),
            "HARDWARE_ANALYSIS_JOB_MAX_PENDING_PER_USER": (
                settings.HARDWARE_ANALYSIS_JOB_MAX_PENDING_PER_USER
            ),
            "HARDWARE_ANALYSIS_JOB_MAX_ATTEMPTS": (
                settings.HARDWARE_ANALYSIS_JOB_MAX_ATTEMPTS
            ),
        }
        settings.ALERT_AUTH_SECRET = "test-hardware-jobs-auth-secret-32"
        settings.ALERT_BOOTSTRAP_TOKEN = "test-hardware-jobs-bootstrap"
        settings.ALERT_SCHEDULER_ENABLED = False
        settings.HARDWARE_ANALYSIS_JOBS_ENABLED = True
        settings.HARDWARE_ANALYSIS_JOB_MAX_PENDING = 50
        settings.HARDWARE_ANALYSIS_JOB_MAX_PENDING_PER_USER = 3
        settings.HARDWARE_ANALYSIS_JOB_MAX_ATTEMPTS = 3

        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)

        def override_get_db():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        self.worker_start_patch = patch(
            "app.main.start_hardware_analysis_workers"
        )
        self.worker_stop_patch = patch(
            "app.main.stop_hardware_analysis_workers"
        )
        self.worker_start_patch.start()
        self.worker_stop_patch.start()
        self.client = TestClient(app)
        bootstrap = self.client.post(
            "/api/monitoring/auth/bootstrap-admin",
            json={
                "email": "hardware-jobs-admin@example.test",
                "password": "secure-test-password",
                "display_name": "Hardware Jobs Admin",
                "bootstrap_token": "test-hardware-jobs-bootstrap",
            },
        )
        self.assertEqual(bootstrap.status_code, 201, bootstrap.text)
        self.client.headers.update(
            {"Authorization": f"Bearer {bootstrap.json()['access_token']}"}
        )

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.pop(get_db, None)
        self.worker_start_patch.stop()
        self.worker_stop_patch.stop()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        for key, value in self.saved_settings.items():
            setattr(settings, key, value)

    def _submit_csv(self, client=None):
        target = client or self.client
        return target.post(
            "/api/hardware/jobs",
            files={
                "file": (
                    "production.csv",
                    b"Product,Production,Defects\nWidget,100,3\n",
                    "text/csv",
                )
            },
            data={"custom_prompt": ""},
        )

    def test_job_submission_and_status_are_persisted(self):
        response = self._submit_csv()

        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["progress"], 0)
        self.assertNotIn("result_data", job)

        status_response = self.client.get(
            f"/api/hardware/jobs/{job['id']}"
        )
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()["id"], job["id"])
        self.assertEqual(status_response.json()["source_filename"], "production.csv")

        list_response = self.client.get("/api/hardware/jobs")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["pagination"]["total"], 1)

    def test_hardware_standards_endpoints_serve_knowledge_base_metadata(self):
        response = self.client.get("/api/hardware/standards")

        self.assertEqual(response.status_code, 200, response.text)
        pack = response.json()
        self.assertEqual(pack["knowledge_base_version"], "1.0.0")
        self.assertTrue(pack["sources"])
        self.assertTrue(pack["license_review"]["sources"])
        self.assertIn(
            "SMBIOS",
            {item["id"] for item in pack["standards"]},
        )

        detected = self.client.post(
            "/api/hardware/standards/detect",
            json={"columns": ["BIOS Version", "Processor Name"]},
        )
        self.assertEqual(detected.status_code, 200, detected.text)
        self.assertIn("SMBIOS", detected.json()["detected_ids"])

    def test_job_submission_queues_without_parsing_the_file(self):
        with patch(
            "app.routes.hardware._parse_hardware_content",
            side_effect=AssertionError("Parsing should run in the worker"),
        ):
            response = self._submit_csv()

        self.assertEqual(response.status_code, 202, response.text)

    def test_job_readiness_error_is_reported_by_the_worker(self):
        response = self.client.post(
            "/api/hardware/jobs",
            files={
                "file": (
                    "date-only.csv",
                    b"Date\n2026-03-01\n",
                    "text/csv",
                )
            },
            data={"custom_prompt": ""},
        )

        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["id"]
        with Session(self.engine) as db:
            job = db.get(HardwareAnalysisJob, job_id)
            assert job is not None
            job.status = "running"
            job.attempt_count = 1
            db.commit()

        session_factory = lambda: Session(self.engine)
        with (
            patch("app.routes.hardware.SessionLocal", session_factory),
            patch(
                "app.services.hardware_analysis_jobs.SessionLocal",
                session_factory,
            ),
        ):
            from app.routes.hardware import process_hardware_analysis_job

            process_hardware_analysis_job(job_id)

        with Session(self.engine) as db:
            job = db.get(HardwareAnalysisJob, job_id)
            assert job is not None
            self.assertEqual(job.status, "failed")
            self.assertIn("File chưa đủ dữ liệu", job.error_message or "")
            self.assertIn("Đã nhận diện: Date.", job.error_message or "")
            self.assertIsNone(job.input_file)

    def test_job_status_is_private_to_its_owner(self):
        job_response = self._submit_csv()
        self.assertEqual(job_response.status_code, 202, job_response.text)
        job_id = job_response.json()["id"]

        with Session(self.engine) as db:
            other_user = AlertUser(
                email="another-operator@example.test",
                display_name="Other Operator",
                password_hash=hash_password("another-secure-password"),
                role="operator",
            )
            db.add(other_user)
            db.flush()
            token = create_access_token(db, other_user)
            db.commit()

        response = self.client.get(
            f"/api/hardware/jobs/{job_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 404)

    def test_job_list_does_not_load_file_blobs_or_analysis_results(self):
        submitted = self._submit_csv()
        self.assertEqual(submitted.status_code, 202, submitted.text)
        statements = []

        def capture_statement(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ):
            if "FROM hardware_analysis_jobs" in statement:
                statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", capture_statement)
        try:
            response = self.client.get("/api/hardware/jobs")
        finally:
            event.remove(
                self.engine,
                "before_cursor_execute",
                capture_statement,
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(statements)
        list_query = statements[-1].casefold()
        self.assertNotIn("input_file", list_query)
        self.assertNotIn("result_data", list_query)

    def test_submission_is_limited_per_user(self):
        settings.HARDWARE_ANALYSIS_JOB_MAX_PENDING_PER_USER = 1
        first = self._submit_csv()
        second = self._submit_csv()

        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(second.status_code, 429, second.text)
        with Session(self.engine) as db:
            count = db.scalar(
                select(func.count()).select_from(HardwareAnalysisJob)
            )
            self.assertEqual(count, 1)

    def test_processor_saves_result_and_analysis_history_atomically(self):
        submitted = self._submit_csv()
        self.assertEqual(submitted.status_code, 202, submitted.text)
        job_id = submitted.json()["id"]
        with Session(self.engine) as db:
            job = db.get(HardwareAnalysisJob, job_id)
            assert job is not None
            job.status = "running"
            job.attempt_count = 1
            db.commit()

        session_factory = lambda: Session(self.engine)
        with (
            patch("app.routes.hardware.SessionLocal", session_factory),
            patch(
                "app.services.hardware_analysis_jobs.SessionLocal",
                session_factory,
            ),
            patch(
                "app.routes.hardware.analyze_hardware_dataframe",
                return_value={
                    "summary": "Production stable.",
                    "data_quality": {"quality_score": 100},
                },
            ),
        ):
            from app.routes.hardware import process_hardware_analysis_job

            process_hardware_analysis_job(job_id)

        with Session(self.engine) as db:
            saved_job = db.get(HardwareAnalysisJob, job_id)
            self.assertIsNotNone(saved_job)
            assert saved_job is not None
            self.assertEqual(saved_job.status, "succeeded")
            self.assertEqual(saved_job.progress, 100)
            self.assertIsNone(saved_job.input_file)
            self.assertIsNotNone(saved_job.result_data["analysis_history_id"])
            history = db.scalar(
                select(AnalysisHistory).where(
                    AnalysisHistory.id
                    == saved_job.result_data["analysis_history_id"]
                )
            )
            self.assertIsNotNone(history)

    def test_plant_scope_is_saved_on_job_and_analysis_history(self):
        with Session(self.engine) as db:
            plant = Plant(name="Plant-scoped job")
            db.add(plant)
            db.commit()
            plant_id = plant.id

        submitted = self.client.post(
            "/api/hardware/jobs",
            files={
                "file": (
                    "production.csv",
                    b"Product,Production,Defects\nWidget,100,3\n",
                    "text/csv",
                )
            },
            data={"custom_prompt": "", "plant_id": str(plant_id)},
        )
        self.assertEqual(submitted.status_code, 202, submitted.text)
        job_id = submitted.json()["id"]
        self.assertEqual(submitted.json()["plant_id"], plant_id)

        with Session(self.engine) as db:
            job = db.get(HardwareAnalysisJob, job_id)
            assert job is not None
            job.status = "running"
            job.attempt_count = 1
            db.commit()

        session_factory = lambda: Session(self.engine)
        with (
            patch("app.routes.hardware.SessionLocal", session_factory),
            patch(
                "app.services.hardware_analysis_jobs.SessionLocal",
                session_factory,
            ),
            patch(
                "app.routes.hardware.analyze_hardware_dataframe",
                return_value={
                    "summary": "Production stable.",
                    "data_quality": {"quality_score": 100},
                },
            ),
        ):
            from app.routes.hardware import process_hardware_analysis_job

            process_hardware_analysis_job(job_id)

        with Session(self.engine) as db:
            job = db.get(HardwareAnalysisJob, job_id)
            assert job is not None
            history = db.get(
                AnalysisHistory,
                job.result_data["analysis_history_id"],
            )
            self.assertIsNotNone(history)
            assert history is not None
            self.assertEqual(history.plant_id, plant_id)

    def test_job_submission_rejects_plant_outside_users_scope(self):
        with Session(self.engine) as db:
            plant = Plant(name="Restricted plant")
            other_user = AlertUser(
                email="restricted-operator@example.test",
                display_name="Restricted Operator",
                password_hash=hash_password("another-secure-password"),
                role="operator",
            )
            db.add_all([plant, other_user])
            db.flush()
            plant_id = plant.id
            token = create_access_token(db, other_user)
            db.commit()
            user_id = other_user.id

        response = self.client.post(
            "/api/hardware/jobs",
            headers={"Authorization": f"Bearer {token}"},
            files={
                "file": (
                    "production.csv",
                    b"Product,Production,Defects\nWidget,100,3\n",
                    "text/csv",
                )
            },
            data={"custom_prompt": "", "plant_id": str(plant_id)},
        )

        self.assertEqual(response.status_code, 403, response.text)
        with Session(self.engine) as db:
            self.assertEqual(
                db.scalar(
                    select(func.count())
                    .select_from(HardwareAnalysisJob)
                    .where(HardwareAnalysisJob.owner_user_id == user_id)
                ),
                0,
            )
            audit = db.scalar(
                select(UserActionAudit).where(
                    UserActionAudit.actor_user_id == user_id,
                    UserActionAudit.object_type == "plant",
                    UserActionAudit.object_id == str(plant_id),
                    UserActionAudit.outcome == "denied",
                )
            )
            self.assertIsNotNone(audit)

    def test_failure_retries_then_clears_uploaded_content_at_terminal_state(self):
        submitted = self._submit_csv()
        self.assertEqual(submitted.status_code, 202, submitted.text)
        job_id = submitted.json()["id"]
        with Session(self.engine) as db:
            job = db.get(HardwareAnalysisJob, job_id)
            assert job is not None
            job.status = "running"
            job.attempt_count = 1
            db.commit()

        session_factory = lambda: Session(self.engine)
        with patch(
            "app.services.hardware_analysis_jobs.SessionLocal",
            session_factory,
        ):
            fail_hardware_analysis_job(job_id, RuntimeError("private detail"))

        with Session(self.engine) as db:
            job = db.get(HardwareAnalysisJob, job_id)
            assert job is not None
            self.assertEqual(job.status, "queued")
            self.assertEqual(job.stage, "retry_wait")
            self.assertIsNotNone(job.input_file)
            self.assertNotIn("private detail", job.error_message or "")
            job.status = "running"
            job.attempt_count = 3
            db.commit()

        with patch(
            "app.services.hardware_analysis_jobs.SessionLocal",
            session_factory,
        ):
            fail_hardware_analysis_job(job_id, RuntimeError("private detail"))

        with Session(self.engine) as db:
            job = db.get(HardwareAnalysisJob, job_id)
            assert job is not None
            self.assertEqual(job.status, "failed")
            self.assertIsNone(job.input_file)
            self.assertIsNotNone(job.completed_at)

    def test_input_error_fails_immediately_with_backend_message(self):
        submitted = self._submit_csv()
        self.assertEqual(submitted.status_code, 202, submitted.text)
        job_id = submitted.json()["id"]
        with Session(self.engine) as db:
            job = db.get(HardwareAnalysisJob, job_id)
            assert job is not None
            job.status = "running"
            job.attempt_count = 1
            db.commit()

        session_factory = lambda: Session(self.engine)
        with patch(
            "app.services.hardware_analysis_jobs.SessionLocal",
            session_factory,
        ):
            fail_hardware_analysis_job(
                job_id,
                ValueError("File chưa đủ dữ liệu cho phân tích sản xuất."),
            )

        with Session(self.engine) as db:
            job = db.get(HardwareAnalysisJob, job_id)
            assert job is not None
            self.assertEqual(job.status, "failed")
            self.assertEqual(
                job.error_message,
                "File chưa đủ dữ liệu cho phân tích sản xuất.",
            )
            self.assertIsNone(job.input_file)
            self.assertEqual(job.attempt_count, 1)

    def test_http_input_error_fails_immediately_with_backend_detail(self):
        submitted = self._submit_csv()
        self.assertEqual(submitted.status_code, 202, submitted.text)
        job_id = submitted.json()["id"]
        with Session(self.engine) as db:
            job = db.get(HardwareAnalysisJob, job_id)
            assert job is not None
            job.status = "running"
            job.attempt_count = 1
            db.commit()

        session_factory = lambda: Session(self.engine)
        with patch(
            "app.services.hardware_analysis_jobs.SessionLocal",
            session_factory,
        ):
            fail_hardware_analysis_job(
                job_id,
                HTTPException(status_code=413, detail="PDF quá lớn."),
            )

        with Session(self.engine) as db:
            job = db.get(HardwareAnalysisJob, job_id)
            assert job is not None
            self.assertEqual(job.status, "failed")
            self.assertEqual(job.error_message, "PDF quá lớn.")
            self.assertIsNone(job.input_file)

    def test_worker_claims_ready_jobs_and_tracks_attempts(self):
        first = self._submit_csv()
        second = self._submit_csv()
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(second.status_code, 202, second.text)

        with patch(
            "app.services.hardware_analysis_jobs.SessionLocal",
            lambda: Session(self.engine),
        ):
            claimed_ids = {_claim_next_job(), _claim_next_job()}

        self.assertEqual(
            claimed_ids,
            {first.json()["id"], second.json()["id"]},
        )
        with Session(self.engine) as db:
            claimed = db.scalars(
                select(HardwareAnalysisJob).where(
                    HardwareAnalysisJob.status == "running"
                )
            ).all()
            self.assertEqual(len(claimed), 2)
            self.assertEqual({job.attempt_count for job in claimed}, {1})

    def test_worker_reclaims_running_jobs_with_expired_or_missing_lease(self):
        submitted_jobs = [self._submit_csv(), self._submit_csv()]
        self.assertTrue(
            all(response.status_code == 202 for response in submitted_jobs)
        )
        job_ids = [response.json()["id"] for response in submitted_jobs]

        with Session(self.engine) as db:
            for job_id, lease_expires_at in zip(
                job_ids,
                (
                    None,
                    datetime.now(timezone.utc) - timedelta(seconds=1),
                ),
            ):
                job = db.get(HardwareAnalysisJob, job_id)
                assert job is not None
                job.status = "running"
                job.attempt_count = 1
                job.lease_expires_at = lease_expires_at
            db.commit()

        with patch(
            "app.services.hardware_analysis_jobs.SessionLocal",
            lambda: Session(self.engine),
        ):
            reclaimed_ids = {_claim_next_job(), _claim_next_job()}

        self.assertEqual(reclaimed_ids, set(job_ids))
        with Session(self.engine) as db:
            reclaimed_jobs = list(
                db.scalars(
                    select(HardwareAnalysisJob).where(
                        HardwareAnalysisJob.id.in_(job_ids)
                    )
                )
            )
            self.assertEqual(
                {job.status for job in reclaimed_jobs},
                {"running"},
            )
            self.assertEqual(
                {job.attempt_count for job in reclaimed_jobs},
                {2},
            )

    def test_worker_shutdown_retains_live_threads_and_prevents_restart(self):
        release_worker = threading.Event()
        worker = threading.Thread(
            target=release_worker.wait,
            name="test-hardware-analysis-worker",
            daemon=True,
        )
        worker.start()
        stop_event = threading.Event()
        with hardware_analysis_jobs._worker_lock:
            previous_stop = hardware_analysis_jobs._worker_stop
            previous_threads = hardware_analysis_jobs._worker_threads
            hardware_analysis_jobs._worker_stop = stop_event
            hardware_analysis_jobs._worker_threads = [worker]

        try:
            with patch.object(worker, "join"):
                stop_hardware_analysis_workers()

            self.assertTrue(stop_event.is_set())
            self.assertTrue(worker.is_alive())
            self.assertIs(hardware_analysis_jobs._worker_stop, stop_event)
            self.assertEqual(hardware_analysis_jobs._worker_threads, [worker])

            with patch.object(
                hardware_analysis_jobs,
                "_worker_loop",
            ) as worker_loop:
                start_hardware_analysis_workers(lambda _job_id: None)

            worker_loop.assert_not_called()
            self.assertEqual(hardware_analysis_jobs._worker_threads, [worker])
        finally:
            release_worker.set()
            worker.join(timeout=2)
            with hardware_analysis_jobs._worker_lock:
                hardware_analysis_jobs._worker_stop = previous_stop
                hardware_analysis_jobs._worker_threads = previous_threads


if __name__ == "__main__":
    unittest.main()
