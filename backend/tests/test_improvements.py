import unittest
from datetime import datetime, timezone
from typing import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models.monitoring import Plant


class ContinuousImprovementTests(unittest.TestCase):
    def setUp(self):
        self.saved_settings = {
            "ALERT_AUTH_SECRET": settings.ALERT_AUTH_SECRET,
            "ALERT_BOOTSTRAP_TOKEN": settings.ALERT_BOOTSTRAP_TOKEN,
            "ALERT_SCHEDULER_ENABLED": settings.ALERT_SCHEDULER_ENABLED,
            "HARDWARE_ANALYSIS_JOBS_ENABLED": (
                settings.HARDWARE_ANALYSIS_JOBS_ENABLED
            ),
        }
        settings.ALERT_AUTH_SECRET = "test-improvement-auth-secret-value-32"
        settings.ALERT_BOOTSTRAP_TOKEN = "test-improvement-bootstrap"
        settings.ALERT_SCHEDULER_ENABLED = False
        settings.HARDWARE_ANALYSIS_JOBS_ENABLED = False
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as db:
            plant = Plant(
                name="Improvement Test Plant",
                timezone="UTC",
                factory_id=1,
            )
            db.add(plant)
            db.commit()
            self.plant_id = plant.id

        def override_get_db() -> Iterator[Session]:
            with Session(self.engine) as db:
                yield db

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        bootstrap = self.client.post(
            "/api/monitoring/auth/bootstrap-admin",
            json={
                "email": "improvement-admin@example.test",
                "password": "secure-improvement-password",
                "display_name": "Improvement Admin",
                "bootstrap_token": "test-improvement-bootstrap",
            },
        )
        self.assertEqual(bootstrap.status_code, 201, bootstrap.text)
        self.user_id = bootstrap.json()["user"]["id"]
        self.client.headers.update(
            {"Authorization": f"Bearer {bootstrap.json()['access_token']}"}
        )

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        for key, value in self.saved_settings.items():
            setattr(settings, key, value)

    def _create_case(self):
        return self.client.post(
            "/api/improvements",
            json={
                "plant_id": self.plant_id,
                "problem": "Defect rate exceeded target",
                "baseline": {
                    "defect_rate": {
                        "value": 4.8,
                        "sample_count": 120,
                        "standard_deviation": 0.4,
                    },
                    "downtime": {
                        "value": 120,
                        "sample_count": 120,
                        "standard_deviation": 12,
                    },
                },
            },
        )

    def test_case_tracks_lifecycle_and_calculates_observed_kpi_changes(self):
        created = self._create_case()
        self.assertEqual(created.status_code, 201, created.text)
        case = created.json()
        self.assertEqual(case["status"], "detected")

        planned = self.client.patch(
            f"/api/improvements/{case['id']}",
            json={
                "root_cause": "Tool wear is suspected",
                "action": "Replace the worn cutting insert",
                "owner_user_id": self.user_id,
            },
        )
        self.assertEqual(planned.status_code, 200, planned.text)
        self.assertEqual(planned.json()["status"], "action_planned")

        measured = self.client.patch(
            f"/api/improvements/{case['id']}",
            json={
                "implemented_at": datetime.now(timezone.utc).isoformat(),
                "after_measurement": {
                    "defect_rate": {
                        "value": 2.9,
                        "sample_count": 125,
                        "standard_deviation": 0.3,
                    },
                    "downtime": {
                        "value": 75,
                        "sample_count": 125,
                        "standard_deviation": 8,
                    },
                },
                "confounders": ["Product mix changed"],
            },
        )
        self.assertEqual(measured.status_code, 200, measured.text)
        body = measured.json()
        self.assertEqual(body["status"], "measured")
        self.assertEqual(
            body["kpi_changes"]["defect_rate"]["absolute_change"],
            -1.9,
        )
        self.assertAlmostEqual(
            body["kpi_changes"]["defect_rate"]["relative_change_percent"],
            -39.5833333333,
        )
        self.assertEqual(body["evidence_confidence"], "well_documented")
        self.assertIn("không tự chứng minh", body["causality_notice"])

        evaluated = self.client.patch(
            f"/api/improvements/{case['id']}",
            json={"conclusion": "Observed improvement; causality not verified."},
        )
        self.assertEqual(evaluated.status_code, 200, evaluated.text)
        self.assertEqual(evaluated.json()["status"], "evaluated")

        closed = self.client.patch(
            f"/api/improvements/{case['id']}",
            json={"close": True},
        )
        self.assertEqual(closed.status_code, 200, closed.text)
        self.assertEqual(closed.json()["status"], "closed")

    def test_measurement_cannot_precede_implementation(self):
        created = self._create_case()
        case_id = created.json()["id"]

        response = self.client.patch(
            f"/api/improvements/{case_id}",
            json={
                "after_measurement": {
                    "defect_rate": {
                        "value": 2.9,
                        "sample_count": 120,
                    }
                }
            },
        )

        self.assertEqual(response.status_code, 409)

    def test_list_is_paginated_and_reports_observed_case_count(self):
        created = self._create_case()
        self.assertEqual(created.status_code, 201, created.text)

        response = self.client.get("/api/improvements?page=1&page_size=1")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(len(response.json()["items"]), 1)
