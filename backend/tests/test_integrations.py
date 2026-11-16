import unittest
from typing import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models import (
    IntegrationConnector,
    IntegrationSyncRun,
    MonitoringDataset,
)
from app.models.monitoring import AlertUser, Plant
from app.services.monitoring_auth import create_access_token, hash_password


class IndustrialFileIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.saved_settings = {
            "ALERT_AUTH_SECRET": settings.ALERT_AUTH_SECRET,
            "ALERT_BOOTSTRAP_TOKEN": settings.ALERT_BOOTSTRAP_TOKEN,
            "ALERT_SCHEDULER_ENABLED": settings.ALERT_SCHEDULER_ENABLED,
            "HARDWARE_ANALYSIS_JOBS_ENABLED": (
                settings.HARDWARE_ANALYSIS_JOBS_ENABLED
            ),
        }
        settings.ALERT_AUTH_SECRET = "test-industrial-integration-auth-secret-32"
        settings.ALERT_BOOTSTRAP_TOKEN = "test-integration-bootstrap"
        settings.ALERT_SCHEDULER_ENABLED = False
        settings.HARDWARE_ANALYSIS_JOBS_ENABLED = False
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)

        with Session(self.engine) as db:
            self.plant = Plant(
                name="Integration Test Plant",
                timezone="UTC",
                factory_id=1,
            )
            db.add(self.plant)
            db.commit()
            self.plant_id = self.plant.id

        def override_get_db() -> Iterator[Session]:
            with Session(self.engine) as db:
                yield db

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        bootstrap = self.client.post(
            "/api/monitoring/auth/bootstrap-admin",
            json={
                "email": "integration-admin@example.test",
                "password": "secure-integration-password",
                "display_name": "Integration Admin",
                "bootstrap_token": "test-integration-bootstrap",
            },
        )
        self.assertEqual(bootstrap.status_code, 201, bootstrap.text)
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

    def _mapping(self):
        return {
            "Work Date": "Date",
            "Good Qty": "Production",
            "Reject Qty": "Defects",
            "Stop Seconds": "Downtime",
            "Machine Code": "Machine",
            "ERP Work Center": "attributes.work_center",
        }

    def _create_connector(self):
        response = self.client.post(
            "/api/integrations/connectors",
            json={
                "name": "MES daily export",
                "plant_id": self.plant_id,
                "column_mapping": self._mapping(),
                "normalization": {
                    "date_timezone": "Asia/Ho_Chi_Minh",
                    "downtime_unit": "seconds",
                    "uppercase_code_fields": ["Machine"],
                },
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _file(self, payload=None):
        content = payload or (
            "Work Date,Good Qty,Reject Qty,Stop Seconds,Machine Code,ERP Work Center\n"
            "2026-11-10,100,2,120,m-01,Line A\n"
        )
        return {
            "file": (
                "mes-export.csv",
                content.encode("utf-8"),
                "text/csv",
            )
        }

    def test_registry_only_advertises_implemented_connectors(self):
        response = self.client.get("/api/integrations/registry")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([item["type"] for item in body["connectors"]], ["file"])
        self.assertEqual(body["not_connected"], ["ERP", "MES", "SCADA", "PLC", "Machine API"])
        self.assertIn("Date", body["required_fields"])
        self.assertIn("Production", body["required_fields"])

    def test_preview_suggests_mapping_without_persisting_source_rows(self):
        response = self.client.post(
            "/api/integrations/preview",
            files=self._file(
                "Date,Production,Machine Code\n2026-11-10,20,M-02\n"
            ),
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["row_count"], 1)
        self.assertEqual(
            body["suggested_mapping"],
            {
                "Date": "Date",
                "Production": "Production",
                "Machine Code": "Machine",
            },
        )
        self.assertEqual(body["sample_rows"][0]["Machine Code"], "M-02")

    def test_connector_requires_date_and_production_mapping(self):
        response = self.client.post(
            "/api/integrations/connectors",
            json={
                "name": "Invalid mapping",
                "plant_id": self.plant_id,
                "column_mapping": {
                    "Quantity": "Production",
                    "Some other field": "attributes.source_field",
                },
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("Date", response.json()["detail"])

    def test_sync_normalizes_data_persists_lineage_and_is_idempotent(self):
        connector = self._create_connector()
        first = self.client.post(
            f"/api/integrations/connectors/{connector['id']}/sync",
            files=self._file(),
        )

        self.assertEqual(first.status_code, 200, first.text)
        body = first.json()
        self.assertEqual(body["status"], "succeeded")
        self.assertEqual(body["rows_imported"], 1)
        self.assertFalse(body["idempotent_replay"])
        self.assertEqual(body["lineage"]["connector_id"], connector["id"])
        self.assertEqual(len(body["source_file_sha256"]), 64)

        replay = self.client.post(
            f"/api/integrations/connectors/{connector['id']}/sync",
            files=self._file(),
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["idempotent_replay"])
        self.assertEqual(replay.json()["id"], body["id"])

        with Session(self.engine) as db:
            dataset = db.get(MonitoringDataset, body["dataset_id"])
            self.assertIsNotNone(dataset)
            assert dataset is not None
            record = dataset.data_rows[0]
            self.assertEqual(record["Production"], 100)
            self.assertEqual(record["Defects"], 2)
            self.assertEqual(record["Downtime"], 2)
            self.assertEqual(record["Machine"], "M-01")
            self.assertEqual(
                record["attributes"]["work_center"],
                "Line A",
            )
            self.assertTrue(record["Date"].startswith("2026-11-09T17:00:00"))
            sync_count = db.scalar(
                select(func.count()).select_from(IntegrationSyncRun)
            )
            dataset_count = db.scalar(
                select(func.count()).select_from(MonitoringDataset)
            )
            self.assertEqual(sync_count, 1)
            self.assertEqual(dataset_count, 1)

    def test_invalid_rows_are_quarantined_in_sync_history_without_import(self):
        connector = self._create_connector()
        response = self.client.post(
            f"/api/integrations/connectors/{connector['id']}/sync",
            files=self._file(
                "Work Date,Good Qty,Reject Qty,Stop Seconds,Machine Code,ERP Work Center\n"
                "2026-11-10,not-a-number,2,120,m-01,Line A\n"
            ),
        )

        self.assertEqual(response.status_code, 422, response.text)
        body = response.json()["detail"]
        self.assertEqual(body["validation_report"]["rows_rejected"], 1)
        self.assertEqual(
            body["validation_report"]["issues"][0]["code"],
            "invalid_number",
        )
        self.assertNotIn("not-a-number", response.text)

        history = self.client.get(
            f"/api/integrations/sync-runs?connector_id={connector['id']}"
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["items"][0]["status"], "failed")
        self.assertEqual(history.json()["items"][0]["rows_rejected"], 1)
        with Session(self.engine) as db:
            dataset_count = db.scalar(
                select(func.count()).select_from(MonitoringDataset)
            )
            self.assertEqual(dataset_count, 0)

    def test_changed_mapping_configuration_creates_a_new_ingestion_version(self):
        connector = self._create_connector()
        first = self.client.post(
            f"/api/integrations/connectors/{connector['id']}/sync",
            files=self._file(),
        )
        self.assertEqual(first.status_code, 200, first.text)

        updated = self.client.patch(
            f"/api/integrations/connectors/{connector['id']}",
            json={
                "normalization": {
                    "date_timezone": "Asia/Ho_Chi_Minh",
                    "downtime_unit": "seconds",
                    "uppercase_code_fields": [],
                }
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)

        second = self.client.post(
            f"/api/integrations/connectors/{connector['id']}/sync",
            files=self._file(),
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertNotEqual(first.json()["id"], second.json()["id"])
        self.assertFalse(second.json()["idempotent_replay"])
        with Session(self.engine) as db:
            self.assertEqual(
                db.scalar(select(func.count()).select_from(MonitoringDataset)),
                2,
            )

    def test_changed_schedule_configuration_creates_a_new_ingestion_version(self):
        connector = self._create_connector()
        first = self.client.post(
            f"/api/integrations/connectors/{connector['id']}/sync",
            files=self._file(),
        )
        self.assertEqual(first.status_code, 200, first.text)

        second = self.client.post(
            f"/api/integrations/connectors/{connector['id']}/sync",
            files=self._file(),
            data={
                "granularity": "week",
                "schedule_interval": "2",
                "schedule_unit": "weeks",
            },
        )

        self.assertEqual(second.status_code, 200, second.text)
        self.assertNotEqual(first.json()["id"], second.json()["id"])
        self.assertFalse(second.json()["idempotent_replay"])
        with Session(self.engine) as db:
            self.assertEqual(
                db.scalar(select(func.count()).select_from(MonitoringDataset)),
                2,
            )

    def test_connector_and_sync_history_are_isolated_by_plant_access(self):
        connector = self._create_connector()
        with Session(self.engine) as db:
            user = AlertUser(
                email="unassigned-operator@example.test",
                display_name="Unassigned Operator",
                password_hash=hash_password("another-secure-password"),
                role="operator",
            )
            db.add(user)
            db.flush()
            token = create_access_token(db, user)
            db.commit()

        response = self.client.get(
            f"/api/integrations/connectors",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

        sync_response = self.client.post(
            f"/api/integrations/connectors/{connector['id']}/sync",
            files=self._file(),
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(sync_response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
