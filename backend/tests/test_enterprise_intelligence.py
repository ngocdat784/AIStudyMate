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
from app.models import AIAuditLog, AnalysisHistory, ImprovementCase, UserActionAudit
from app.models.monitoring import Alert, AlertUser, Plant
from app.services.monitoring_auth import create_access_token


_DOMAIN_RESULTS = {
    "production_analysis": {
        "analysis_limitations": [],
        "recommendations": [
            {
                "action": "Check line output and validate the production plan.",
                "priority": "high",
                "evidence_ids": ["E0001"],
            }
        ],
    },
    "quality_analysis": {
        "analysis_limitations": [],
        "recommendations": [
            {
                "action": "Investigate the leading defect category.",
                "priority": "medium",
            }
        ],
    },
    "machine_intelligence": {
        "analysis_limitations": [],
        "maintenance_priorities": [
            {
                "machine": "Press 1",
                "risk": "high",
                "failure_count": 4,
                "unplanned_downtime_minutes": 120,
                "recommendation": "Inspect repeated faults on Press 1.",
            }
        ],
    },
    "material_intelligence": {
        "analysis_limitations": [],
        "supplier_quality": [
            {
                "supplier": "Supplier A",
                "risk_level": "high",
                "defect_rate_percent": 7.5,
                "evidence_id": "E0003",
            }
        ],
        "material_performance": [
            {
                "material": "Resin",
                "unit": "g",
                "material_loss": 12,
                "waste_rate_percent": 6,
            }
        ],
    },
    "predictive_intelligence": {
        "analysis_limitations": [],
        "material_shortage_risk": [
            {
                "material": "Resin",
                "unit": "g",
                "status": "shortage_signal",
                "shortage_quantity": 20,
            }
        ],
        "quality_risk": {
            "threshold_signals": [
                {
                    "period": "2026-11-17",
                    "value": 8,
                    "threshold_percent": 5,
                    "status": "threshold_exceedance_signal",
                }
            ]
        },
        "production_plan_completion": {
            "daily_assessment": [
                {
                    "period": "2026-11-17",
                    "value": 90,
                    "status": "plan_shortfall_signal",
                }
            ]
        },
    },
    "root_cause_intelligence": {
        "analysis_limitations": [],
        "corrective_actions": [
            {
                "action": "Validate the suspected process factor with a controlled check.",
                "priority": "medium",
                "evidence_ids": ["E0002"],
            }
        ],
    },
}


class EnterpriseIntelligenceAPITests(unittest.TestCase):
    def setUp(self):
        self.saved_settings = {
            "ALERT_AUTH_SECRET": settings.ALERT_AUTH_SECRET,
            "ALERT_BOOTSTRAP_TOKEN": settings.ALERT_BOOTSTRAP_TOKEN,
            "ALERT_SCHEDULER_ENABLED": settings.ALERT_SCHEDULER_ENABLED,
            "HARDWARE_ANALYSIS_JOBS_ENABLED": (
                settings.HARDWARE_ANALYSIS_JOBS_ENABLED
            ),
        }
        settings.ALERT_AUTH_SECRET = "test-enterprise-auth-secret-value-32"
        settings.ALERT_BOOTSTRAP_TOKEN = "test-enterprise-bootstrap"
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
                "email": "enterprise-admin@example.test",
                "password": "secure-enterprise-password",
                "display_name": "Enterprise Admin",
                "bootstrap_token": "test-enterprise-bootstrap",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.admin_token = response.json()["access_token"]
        self.client.headers.update(
            {"Authorization": f"Bearer {self.admin_token}"}
        )

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        for key, value in self.saved_settings.items():
            setattr(settings, key, value)

    def _seed_enterprise_data(self):
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session:
            admin = session.scalar(
                select(AlertUser).where(
                    AlertUser.email == "enterprise-admin@example.test"
                )
            )
            assert admin is not None
            for analysis_type, result_data in _DOMAIN_RESULTS.items():
                session.add(
                    AnalysisHistory(
                        analysis_type=analysis_type,
                        title=analysis_type,
                        summary=f"Latest {analysis_type} result",
                        result_data=result_data,
                        creator_user_id=admin.id,
                        algorithm_version=f"{analysis_type}-v1",
                        ai_status="not_requested",
                        evidence=[{"id": "E0001"}],
                        source_file_sha256="a" * 64,
                        created_at=now - timedelta(days=1),
                    )
                )
            plant = Plant(name="Enterprise Plant")
            session.add(plant)
            session.flush()
            session.add(
                Alert(
                    dedupe_key="b" * 64,
                    alert_type="threshold",
                    plant_id=plant.id,
                    metric="defect_rate",
                    scope_type="plant",
                    severity="high",
                    title="Defect rate exceeded",
                    summary="Observed defect rate is above threshold.",
                    observed_value=8,
                    threshold_value=5,
                    impact={"defects": 8},
                    evidence=[],
                    recommended_check="Check inspection records.",
                    confidence="high",
                    status="acknowledged",
                )
            )
            session.add(
                ImprovementCase(
                    id="overdue-enterprise-case",
                    plant_id=plant.id,
                    created_by_user_id=admin.id,
                    owner_user_id=admin.id,
                    problem="Reduce repeated downtime.",
                    baseline={"downtime": {"value": 120, "sample_count": 30}},
                    root_cause="Unverified mechanical wear.",
                    action="Inspect the press.",
                    action_priority="high",
                    action_due_at=now - timedelta(days=1),
                    status="action_planned",
                )
            )
            session.add(
                UserActionAudit(
                    actor_user_id=admin.id,
                    action="improvement.case_created",
                    object_type="improvement_case",
                    object_id="overdue-enterprise-case",
                    outcome="success",
                )
            )
            session.add(
                AIAuditLog(
                    analysis_type="quality_analysis",
                    model_name="gemini-test",
                    prompt_version="test-v1",
                    status="success",
                    validation_status="passed",
                )
            )
            session.commit()

    def test_overview_unifies_six_domains_alerts_cases_and_traceability(self):
        self._seed_enterprise_data()

        response = self.client.get(
            "/api/enterprise-intelligence/overview?days=30"
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        overview = body["enterprise_overview"]
        self.assertEqual(overview["domain_count"], 6)
        self.assertEqual(overview["analyzed_domain_count"], 6)
        self.assertEqual(overview["active_alert_count"], 1)
        self.assertEqual(overview["active_work_item_count"], 1)
        self.assertEqual(overview["overdue_work_item_count"], 1)
        self.assertEqual(overview["ranked_recommendation_count"], 9)
        supplier_recommendation = next(
            item
            for item in body["recommendations"]
            if "Supplier A" in item["action"]
        )
        self.assertEqual(supplier_recommendation["priority"], "high")
        self.assertEqual(supplier_recommendation["evidence_ids"], ["E0003"])
        priorities = [
            item["priority"] for item in body["recommendations"]
        ]
        self.assertEqual(
            priorities,
            sorted(priorities, key={"high": 0, "medium": 1, "low": 2}.get),
        )
        self.assertEqual(len(body["audit_governance"]["traceability"]), 6)
        self.assertEqual(
            body["audit_governance"]["ai_analysis_audit"]["status"],
            "available",
        )
        self.assertEqual(
            body["workflow"]["overdue_cases"][0]["escalation_status"],
            "overdue_escalation_required",
        )
        executive_template = next(
            item
            for item in body["reporting"]["report_templates"]
            if item["key"] == "executive_manufacturing"
        )
        self.assertEqual(executive_template["status"], "ready")

    def test_non_admin_only_sees_own_analyses_and_activity(self):
        self._seed_enterprise_data()
        with Session(self.engine) as session:
            viewer = AlertUser(
                email="enterprise-viewer@example.test",
                display_name="Enterprise Viewer",
                password_hash="not-used-by-session-token",
                role="viewer",
                is_active=True,
                account_status="active",
            )
            session.add(viewer)
            session.flush()
            viewer_history = AnalysisHistory(
                analysis_type="production_analysis",
                title="Viewer production",
                result_data={"analysis_limitations": []},
                creator_user_id=viewer.id,
                created_at=datetime.now(timezone.utc),
            )
            session.add(viewer_history)
            session.add(
                UserActionAudit(
                    actor_user_id=viewer.id,
                    action="history.viewed",
                    object_type="analysis_history",
                    object_id="1",
                    outcome="success",
                )
            )
            token = create_access_token(session, viewer)
            session.commit()

        response = self.client.get(
            "/api/enterprise-intelligence/overview",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        production = next(
            item for item in body["domains"] if item["key"] == "production"
        )
        self.assertEqual(
            production["latest_analysis"]["title"],
            "Viewer production",
        )
        self.assertTrue(
            all(
                item["actor_user_id"] is not None
                for item in body["audit_governance"]["user_activity"]
            )
        )
        self.assertEqual(
            body["audit_governance"]["ai_analysis_audit"]["status"],
            "admin_only",
        )
        self.assertEqual(body["intelligent_alerts"], [])

    def test_analysis_outside_selected_window_is_stale_not_recommended(self):
        with Session(self.engine) as session:
            admin = session.scalar(
                select(AlertUser).where(
                    AlertUser.email == "enterprise-admin@example.test"
                )
            )
            assert admin is not None
            session.add(
                AnalysisHistory(
                    analysis_type="production_analysis",
                    title="Old production analysis",
                    result_data={
                        "recommendations": [
                            {"action": "Recheck the old production plan."}
                        ]
                    },
                    creator_user_id=admin.id,
                    created_at=datetime.now(timezone.utc) - timedelta(days=40),
                )
            )
            session.commit()

        response = self.client.get(
            "/api/enterprise-intelligence/overview?days=30"
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        production = next(
            item for item in body["domains"] if item["key"] == "production"
        )
        self.assertEqual(production["status"], "stale")
        self.assertEqual(production["recommendations"], [])
        self.assertEqual(
            body["enterprise_overview"]["analyzed_domain_count"],
            0,
        )
