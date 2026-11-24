import hashlib
import json
import unittest
from datetime import date, timedelta
from typing import Iterator

import pandas as pd
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models import AnalysisHistory, UserActionAudit
from app.models.monitoring import AlertUser


def _integrated_frame(days: int = 40) -> pd.DataFrame:
    start = date(2026, 9, 1)
    return pd.DataFrame(
        [
            {
                "Date": (start + timedelta(days=index)).isoformat(),
                "Product": "Widget",
                "Process": "Seal",
                "Line": "Line A",
                "Shift": "Day",
                "Machine": "Sealer 1",
                "Machine Status": "Fault" if index % 5 == 0 else "Running",
                "Downtime Type": "Mechanical fault" if index % 5 == 0 else None,
                "Downtime Planned": False,
                "Material": "Resin",
                "Material Lot": "Lot A" if index % 2 else "Lot B",
                "Supplier": "Supplier A" if index % 2 else "Supplier B",
                "Production": 180 - 2 * index,
                "Planned Production": 180,
                "Inspected": 100,
                "Defective Units": 1 + index,
                "Downtime": 2 + index,
                "Available Time": 480,
                "Operating Time": 470 - index,
                "Standard Cycle Time": 120,
                "Cycle Time": 120 + index,
                "Material Unit": "kg",
                "Actual Consumption": 2 + index / 100,
                "Planned Consumption": 2,
                "Material Loss": index / 100,
                "Material Stock": 100,
            }
            for index in range(days)
        ]
    )


class SystemIntegrationAPITests(unittest.TestCase):
    def setUp(self):
        self.saved_settings = {
            "ALERT_AUTH_SECRET": settings.ALERT_AUTH_SECRET,
            "ALERT_BOOTSTRAP_TOKEN": settings.ALERT_BOOTSTRAP_TOKEN,
            "ALERT_SCHEDULER_ENABLED": settings.ALERT_SCHEDULER_ENABLED,
            "HARDWARE_ANALYSIS_JOBS_ENABLED": (
                settings.HARDWARE_ANALYSIS_JOBS_ENABLED
            ),
        }
        settings.ALERT_AUTH_SECRET = "test-system-integration-auth-secret-32"
        settings.ALERT_BOOTSTRAP_TOKEN = "test-system-integration-bootstrap"
        settings.ALERT_SCHEDULER_ENABLED = False
        settings.HARDWARE_ANALYSIS_JOBS_ENABLED = False
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
        self.client = TestClient(app)
        response = self.client.post(
            "/api/monitoring/auth/bootstrap-admin",
            json={
                "email": "system-integration-admin@example.test",
                "password": "secure-system-integration-password",
                "display_name": "System Integration Admin",
                "bootstrap_token": "test-system-integration-bootstrap",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.client.headers.update(
            {"Authorization": f"Bearer {response.json()['access_token']}"}
        )

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        for key, value in self.saved_settings.items():
            setattr(settings, key, value)

    def test_integrated_upload_runs_six_domains_links_evidence_and_saves_history(self):
        dataframe = _integrated_frame().rename(columns={"Production": "Qty"})
        content = dataframe.to_csv(index=False).encode("utf-8")
        column_mapping = {
            column: "Production" if column == "Qty" else column
            for column in dataframe.columns
        }
        response = self.client.post(
            "/api/system-integration/analyze",
            files={"file": ("integrated.csv", content, "text/csv")},
            data={
                "problem_statement": "Assess production, machine and quality risk.",
                "column_mapping": json.dumps(column_mapping),
                "include_ai": "false",
                "horizon": "3",
            },
        )

        self.assertEqual(response.status_code, 201, response.text)
        result = response.json()
        self.assertEqual(result["analysis_type"], "system_integration")
        self.assertEqual(
            set(result["domains"]),
            {
                "production",
                "quality",
                "root_cause",
                "machine",
                "material",
                "predictive",
            },
        )
        self.assertEqual(
            result["analysis_contract"]["domain_order"],
            [
                "production",
                "quality",
                "root_cause",
                "machine",
                "material",
                "predictive",
            ],
        )
        self.assertEqual(
            result["domains"]["predictive"]["status"],
            "limited",
        )
        self.assertEqual(
            result["domains"]["predictive"]["result"]["forecasts"]["production"][
                "status"
            ],
            "forecasted",
        )
        findings = result["cross_intelligence"]["findings"]
        self.assertTrue(
            any(
                item["type"] == "cross_domain_numeric_association"
                and set(item["domains"]) == {"production", "machine"}
                for item in findings
            )
        )
        self.assertTrue(
            any(
                item["type"] == "material_lot_quality_association"
                for item in findings
            )
        )
        self.assertTrue(
            any(
                item["type"] == "machine_predictive_production_review"
                for item in findings
            )
        )
        evidence_ids = {
            item["id"] for item in result["evidence_references"]
        }
        self.assertEqual(len(evidence_ids), len(result["evidence_references"]))
        for finding in findings:
            self.assertTrue(set(finding["evidence_ids"]).issubset(evidence_ids))
            self.assertNotEqual(finding["status"], "causal")
        self.assertEqual(result["ai_orchestration"]["status"], "not_requested")

        history_id = result["analysis_history_id"]
        with Session(self.engine) as session:
            history = session.get(AnalysisHistory, history_id)
            admin = session.scalar(
                select(AlertUser).where(
                    AlertUser.email == "system-integration-admin@example.test"
                )
            )
            self.assertIsNotNone(history)
            self.assertIsNotNone(admin)
            self.assertEqual(history.analysis_type, "system_integration")
            self.assertEqual(history.creator_user_id, admin.id)
            self.assertEqual(
                history.source_file_sha256,
                hashlib.sha256(content).hexdigest(),
            )
            audit = session.scalar(
                select(UserActionAudit).where(
                    UserActionAudit.action
                    == "analysis.system_integration.completed"
                )
            )
            self.assertIsNotNone(audit)
            self.assertEqual(audit.object_id, str(history_id))
            self.assertEqual(audit.after_data["domain_count"], 6)

        history_response = self.client.get(f"/api/history/{history_id}")
        self.assertEqual(history_response.status_code, 200)
        self.assertEqual(
            history_response.json()["result_data"]["analysis_type"],
            "system_integration",
        )

    def test_integrated_endpoint_requires_authentication(self):
        self.client.headers.pop("Authorization", None)
        content = _integrated_frame(days=1).to_csv(index=False).encode("utf-8")

        response = self.client.post(
            "/api/system-integration/analyze",
            files={"file": ("unauthorized.csv", content, "text/csv")},
        )

        self.assertEqual(response.status_code, 401, response.text)

    def test_integrated_endpoint_rejects_missing_production_measurement(self):
        content = pd.DataFrame(
            {"Inspected": [100], "Defective Units": [2]}
        ).to_csv(index=False).encode("utf-8")

        response = self.client.post(
            "/api/system-integration/analyze",
            files={"file": ("quality-only.csv", content, "text/csv")},
            data={"include_ai": "false"},
        )

        self.assertEqual(response.status_code, 422, response.text)

    def test_integrated_endpoint_bounds_multiengine_workload(self):
        content = _integrated_frame(days=2001).to_csv(index=False).encode("utf-8")

        response = self.client.post(
            "/api/system-integration/analyze",
            files={"file": ("large.csv", content, "text/csv")},
            data={"include_ai": "false"},
        )

        self.assertEqual(response.status_code, 413, response.text)

    def test_cross_intelligence_returns_limited_status_for_sparse_history(self):
        content = _integrated_frame(days=5).to_csv(index=False).encode("utf-8")
        response = self.client.post(
            "/api/system-integration/analyze",
            files={"file": ("sparse.csv", content, "text/csv")},
            data={"include_ai": "false"},
        )

        self.assertEqual(response.status_code, 201, response.text)
        result = response.json()
        self.assertEqual(
            result["domains"]["predictive"]["status"],
            "insufficient_data",
        )
        self.assertEqual(
            result["cross_intelligence"]["status"],
            "traceability_only",
        )
        self.assertFalse(
            any(
                item["type"] == "cross_domain_numeric_association"
                for item in result["cross_intelligence"]["findings"]
            )
        )


if __name__ == "__main__":
    unittest.main()
