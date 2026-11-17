import unittest
from datetime import datetime, timedelta, timezone
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
from app.models import AnalysisHistory
from app.services.root_cause_intelligence_service import (
    EvidenceStatement,
    RootCauseAIReport,
    _validate_ai_report,
    analyze_root_cause_intelligence,
)


def _combined_frame(size: int = 12) -> pd.DataFrame:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return pd.DataFrame(
        [
            {
                "Date": (start + timedelta(days=index)).isoformat(),
                "Product": "Widget",
                "Process": "Seal",
                "Line": "Line A",
                "Shift": "Day",
                "Machine": "Sealer 1",
                "Material": "Lot A" if index % 2 else "Lot B",
                "Operator": "Operator A" if index % 2 else "Operator B",
                "Method": "Standard",
                "Environment": "Indoor",
                "Production": 100 + index,
                "Planned Production": 120,
                "Inspected": 100,
                "Defective Units": 5 + index,
                "Downtime": 10 + index * 2,
                "Cycle Time": 20 + index * 3,
                "Waiting Time": index,
                "Ambient Temperature": 20 + index,
            }
            for index in range(size)
        ]
    )


class RootCauseAnalyticsTests(unittest.TestCase):
    def test_correlations_six_m_and_ranked_actions_use_linked_evidence(self):
        result = analyze_root_cause_intelligence(
            _combined_frame(),
            problem_statement="Defect rate is increasing on the sealing process.",
            include_ai=False,
        )

        matrix = result["correlation_matrix"]
        downtime_index = matrix["variables"].index("downtime_minutes")
        defect_rate_index = matrix["variables"].index("defect_rate")
        self.assertAlmostEqual(
            matrix["pearson"][downtime_index][defect_rate_index],
            1.0,
        )
        self.assertAlmostEqual(
            matrix["spearman"][downtime_index][defect_rate_index],
            1.0,
        )
        self.assertEqual(len(result["fishbone_analysis"]), 6)
        environment = next(
            item
            for item in result["fishbone_analysis"]
            if item["category"] == "Mother Nature / Environment"
        )
        self.assertEqual(environment["status"], "evidence_available")
        self.assertTrue(result["root_cause_candidates"])
        self.assertTrue(result["corrective_actions"])
        self.assertTrue(
            all(
                candidate["causality_status"] == "association_only"
                for candidate in result["root_cause_candidates"]
            )
        )
        evidence_ids = {
            item["id"] for item in result["evidence_references"]
        }
        self.assertTrue(
            set(result["root_cause_candidates"][0]["evidence_ids"])
            <= evidence_ids
        )
        self.assertFalse(
            result["confidence_assessment"]["causality_established"]
        )

    def test_separate_production_and_quality_files_link_on_shared_keys(self):
        combined = _combined_frame(size=8)
        production = combined[
            [
                "Date",
                "Product",
                "Process",
                "Line",
                "Shift",
                "Production",
                "Downtime",
            ]
        ]
        quality = combined[
            [
                "Date",
                "Product",
                "Process",
                "Line",
                "Shift",
                "Inspected",
                "Defective Units",
            ]
        ]

        result = analyze_root_cause_intelligence(
            production_dataframe=production,
            quality_dataframe=quality,
            problem_statement="Production and quality records must be linked.",
            include_ai=False,
        )

        self.assertEqual(result["data_scope"]["input_mode"], "split_files")
        self.assertEqual(result["data_scope"]["linked_observation_count"], 8)
        self.assertEqual(
            result["data_scope"]["data_quality"]["rows_read"],
            16,
        )
        self.assertIn("actual_production", result["correlation_matrix"]["variables"])
        self.assertIn("defect_rate", result["correlation_matrix"]["variables"])

    def test_quality_only_factor_does_not_report_zero_production(self):
        combined = _combined_frame(size=8)
        quality = combined[
            [
                "Date",
                "Product",
                "Process",
                "Line",
                "Shift",
                "Inspected",
                "Defective Units",
            ]
        ].copy()
        unmatched = quality.iloc[-1].to_dict()
        unmatched["Date"] = "2026-01-09T00:00:00+00:00"
        unmatched["Line"] = "Quality-only line"
        quality = pd.concat([quality, pd.DataFrame([unmatched])], ignore_index=True)

        result = analyze_root_cause_intelligence(
            production_dataframe=combined[
                ["Date", "Product", "Process", "Line", "Shift", "Production"]
            ],
            quality_dataframe=quality,
            problem_statement="Check production totals for unlinked quality factors.",
            group_dimensions=["line"],
            include_ai=False,
        )

        machine = next(
            item
            for item in result["fishbone_analysis"]
            if item["category"] == "Machine"
        )
        line_factor = next(
            item
            for item in machine["factor_fields"]
            if item["field"] == "Line"
        )
        quality_only = next(
            item
            for item in line_factor["levels"]
            if item["level"] == "Quality-only line"
        )
        self.assertIsNone(quality_only["production"])

    def test_missing_quality_denominator_does_not_create_ranked_root_cause(self):
        result = analyze_root_cause_intelligence(
            pd.DataFrame(
                {
                    "Date": [f"2026-01-{day:02d}" for day in range(1, 9)],
                    "Production": list(range(10, 18)),
                    "Downtime": list(range(1, 9)),
                }
            ),
            problem_statement="Production varies between periods.",
            include_ai=False,
        )

        self.assertEqual(result["root_cause_candidates"][0]["support_score"], 0)
        self.assertEqual(
            result["root_cause_candidates"][0]["causality_status"],
            "unranked",
        )
        self.assertIn(
            "Không ánh xạ được chỉ số chất lượng; không thể phân tích defect rate.",
            result["analysis_limitations"],
        )

    def test_ai_report_requires_existing_evidence_and_no_unlinked_numbers(self):
        statement = {
            "text": "The inspection pattern supports further verification.",
            "evidence_ids": ["E0001"],
        }
        report = RootCauseAIReport(
            problem_statement=statement,
            evidence_summary=[statement],
            fishbone_analysis=[statement],
            five_whys=[statement] * 5,
            root_cause_candidates=[statement],
            corrective_actions=[statement],
            confidence_assessment=statement,
            verification_plan=statement,
            limitations=["The dataset is observational."],
        )

        _validate_ai_report(report, {"E0001"})
        with self.assertRaises(ValueError):
            _validate_ai_report(report, set())
        numeric_report = report.model_copy(
            update={
                "problem_statement": EvidenceStatement(
                    text="The measured rate is 5 percent.",
                    evidence_ids=["E0001"],
                )
            }
        )
        with self.assertRaises(ValueError):
            _validate_ai_report(numeric_report, {"E0001"})


class RootCauseAnalysisAPITests(unittest.TestCase):
    def setUp(self):
        self.saved_settings = {
            "ALERT_AUTH_SECRET": settings.ALERT_AUTH_SECRET,
            "ALERT_BOOTSTRAP_TOKEN": settings.ALERT_BOOTSTRAP_TOKEN,
            "ALERT_SCHEDULER_ENABLED": settings.ALERT_SCHEDULER_ENABLED,
            "HARDWARE_ANALYSIS_JOBS_ENABLED": (
                settings.HARDWARE_ANALYSIS_JOBS_ENABLED
            ),
        }
        settings.ALERT_AUTH_SECRET = "test-root-cause-auth-secret-value-32"
        settings.ALERT_BOOTSTRAP_TOKEN = "test-root-cause-bootstrap"
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
                "email": "root-cause-admin@example.test",
                "password": "secure-root-cause-password",
                "display_name": "Root Cause Admin",
                "bootstrap_token": "test-root-cause-bootstrap",
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

    def test_upload_returns_root_cause_report_and_persists_history(self):
        frame = _combined_frame(size=8)
        content = frame.to_csv(index=False).encode("utf-8")
        response = self.client.post(
            "/api/root-cause/analyze",
            data={
                "problem_statement": "Defect rate is elevated.",
                "include_ai": "false",
                "group_dimensions": "[]",
            },
            files={"file": ("combined.csv", content, "text/csv")},
        )

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["schema_version"], "2.3")
        self.assertEqual(body["problem_statement"], "Defect rate is elevated.")
        self.assertTrue(body["analysis_history_id"])
        with Session(self.engine) as session:
            history = session.scalar(
                select(AnalysisHistory).where(
                    AnalysisHistory.id == body["analysis_history_id"]
                )
            )
            self.assertIsNotNone(history)
            assert history is not None
            self.assertEqual(history.analysis_type, "root_cause_intelligence")
            self.assertEqual(
                history.algorithm_version,
                "root-cause-intelligence-v2.3",
            )

    def test_upload_requires_authentication(self):
        unauthenticated = TestClient(app)
        try:
            response = unauthenticated.post(
                "/api/root-cause/analyze",
                data={"problem_statement": "A production issue."},
                files={
                    "file": (
                        "input.csv",
                        b"Date,Production\n2026-01-01,10\n",
                        "text/csv",
                    )
                },
            )
        finally:
            unauthenticated.close()
        self.assertEqual(response.status_code, 401)

    def test_api_links_separate_production_and_quality_uploads(self):
        frame = _combined_frame(size=8)
        production_csv = frame[
            ["Date", "Product", "Process", "Line", "Shift", "Production"]
        ].to_csv(index=False).encode("utf-8")
        quality_csv = frame[
            ["Date", "Product", "Process", "Line", "Shift", "Inspected", "Defective Units"]
        ].to_csv(index=False).encode("utf-8")

        response = self.client.post(
            "/api/root-cause/analyze",
            data={
                "problem_statement": "Separate source files need temporal linkage.",
                "include_ai": "false",
                "group_dimensions": "[]",
            },
            files={
                "production_file": (
                    "production.csv",
                    production_csv,
                    "text/csv",
                ),
                "quality_file": ("quality.csv", quality_csv, "text/csv"),
            },
        )

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["data_scope"]["input_mode"], "split_files")
        self.assertEqual(body["data_scope"]["source_files"], ["production.csv", "quality.csv"])
        self.assertEqual(body["data_scope"]["linked_observation_count"], 8)
