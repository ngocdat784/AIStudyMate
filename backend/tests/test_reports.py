import unittest
from typing import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models.analysis_history import AnalysisHistory
from app.models.monitoring import UserActionAudit


class GovernedReportAPITests(unittest.TestCase):
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
        settings.ALERT_AUTH_SECRET = "test-governed-reports-auth-secret-32"
        settings.ALERT_BOOTSTRAP_TOKEN = "test-governed-reports-bootstrap"
        self.client = TestClient(app)
        response = self.client.post(
            "/api/monitoring/auth/bootstrap-admin",
            json={
                "email": "report-admin@example.test",
                "password": "secure-test-password",
                "display_name": "Report Admin",
                "bootstrap_token": "test-governed-reports-bootstrap",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.admin_token = response.json()["access_token"]
        self.admin_headers = {"Authorization": f"Bearer {self.admin_token}"}
        self.organization_id, self.factory_id, self.plant_id = self._create_scope(
            "First"
        )
        self.admin_history_id = self._create_history(1, plant_id=self.plant_id)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.pop(get_db, None)
        settings.ALERT_AUTH_SECRET = self.old_secret
        settings.ALERT_BOOTSTRAP_TOKEN = self.old_bootstrap
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _create_scope(self, suffix: str) -> tuple[int, int, int]:
        organization = self.client.post(
            "/api/monitoring/organizations",
            headers=self.admin_headers,
            json={"name": f"Organization {suffix}"},
        )
        self.assertEqual(organization.status_code, 201, organization.text)
        factory = self.client.post(
            "/api/monitoring/factories",
            headers=self.admin_headers,
            json={"organization_id": organization.json()["id"], "name": f"Factory {suffix}"},
        )
        self.assertEqual(factory.status_code, 201, factory.text)
        plant = self.client.post(
            "/api/monitoring/plants",
            headers=self.admin_headers,
            json={"name": f"Plant {suffix}", "factory_id": factory.json()["id"]},
        )
        self.assertEqual(plant.status_code, 201, plant.text)
        return organization.json()["id"], factory.json()["id"], plant.json()["id"]

    def _create_operator(self) -> tuple[int, str]:
        created = self.client.post(
            "/api/monitoring/users",
            headers=self.admin_headers,
            json={
                "email": "report-operator@example.test",
                "password": "secure-test-password",
                "display_name": "Report Operator",
                "role": "operator",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        user_id = created.json()["id"]
        assigned = self.client.put(
            f"/api/monitoring/users/{user_id}/organization-scope",
            headers=self.admin_headers,
            json={
                "factory_ids": [self.factory_id],
                "department_ids": [],
                "plant_ids": [self.plant_id],
            },
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)
        login = self.client.post(
            "/api/monitoring/auth/login",
            json={
                "email": "report-operator@example.test",
                "password": "secure-test-password",
            },
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.operator_history_id = self._create_history(
            user_id,
            plant_id=self.plant_id,
        )
        return user_id, login.json()["access_token"]

    def _create_history(
        self,
        creator_user_id: int,
        plant_id: int | None = None,
    ) -> int:
        with Session(self.engine) as session:
            history = AnalysisHistory(
                analysis_type="hardware_analysis",
                title="Source analysis",
                source_filename="source.csv",
                creator_user_id=creator_user_id,
                plant_id=plant_id,
                result_data={"summary": "Source data"},
            )
            session.add(history)
            session.commit()
            return history.id

    def _create_report(
        self,
        headers: dict[str, str],
        plant_id: int | None = None,
        organization_id: int | None = None,
        factory_id: int | None = None,
        source_history_id: int | None = None,
        include_plant: bool = True,
    ):
        response = self.client.post(
            "/api/reports",
            headers=headers,
            json={
                "title": "Quality report",
                "organization_id": organization_id or self.organization_id,
                "factory_id": factory_id or self.factory_id,
                "plant_id": (
                    self.plant_id
                    if include_plant and plant_id is None
                    else plant_id
                ),
                "content": {
                    "title": "HardwareAI Report",
                    "summary": "Quality results",
                    "kpis": [],
                },
                "source_data": [{
                    "analysis_history_id": (
                        source_history_id
                        or (
                            self.admin_history_id
                            if headers == self.admin_headers
                            else self.operator_history_id
                        )
                    )
                }],
            },
        )
        return response

    def test_operator_report_requires_scope_and_admin_controls_publication(self):
        _operator_id, token = self._create_operator()
        operator_headers = {"Authorization": f"Bearer {token}"}
        created = self._create_report(operator_headers)
        self.assertEqual(created.status_code, 201, created.text)
        report_id = created.json()["id"]

        self.assertEqual(
            self.client.post(
                f"/api/reports/{report_id}/review",
                headers=operator_headers,
                json={"decision": "approved"},
            ).status_code,
            403,
        )
        submitted = self.client.post(
            f"/api/reports/{report_id}/submit",
            headers=operator_headers,
        )
        self.assertEqual(submitted.status_code, 200, submitted.text)
        not_published = self.client.get(
            f"/api/reports/{report_id}/export?format=pdf",
            headers=operator_headers,
        )
        self.assertEqual(not_published.status_code, 409)

        approved = self.client.post(
            f"/api/reports/{report_id}/review",
            headers=self.admin_headers,
            json={"decision": "approved"},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        published = self.client.post(
            f"/api/reports/{report_id}/publish",
            headers=self.admin_headers,
        )
        self.assertEqual(published.status_code, 200, published.text)
        exported = self.client.get(
            f"/api/reports/{report_id}/export?format=pdf",
            headers=operator_headers,
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertTrue(exported.content.startswith(b"%PDF-"))
        with Session(self.engine) as session:
            audited_actions = set(
                session.scalars(
                    select(UserActionAudit.action).where(
                        UserActionAudit.object_type.in_(
                            {"governed_report", "governed_report_version"}
                        ),
                        UserActionAudit.object_id.in_(
                            {str(report_id), str(submitted.json()["version"]["id"])}
                        ),
                    )
                ).all()
            )
        self.assertTrue(
            {
                "report.created",
                "report.submitted",
                "report.approved",
                "report.published",
                "report.exported",
            }.issubset(audited_actions),
            audited_actions,
        )

    def test_published_version_is_immutable_and_edits_create_new_version(self):
        _operator_id, token = self._create_operator()
        headers = {"Authorization": f"Bearer {token}"}
        created = self._create_report(headers)
        report_id = created.json()["id"]
        self.client.post(f"/api/reports/{report_id}/submit", headers=headers)
        self.client.post(
            f"/api/reports/{report_id}/review",
            headers=self.admin_headers,
            json={"decision": "approved"},
        )
        self.client.post(f"/api/reports/{report_id}/publish", headers=self.admin_headers)

        version_two = self.client.post(
            f"/api/reports/{report_id}/versions",
            headers=headers,
            json={
                "title": "Revised quality report",
                "content": {"title": "New content", "summary": "Revised"},
                "source_data": [{"analysis_history_id": self.operator_history_id}],
            },
        )
        self.assertEqual(version_two.status_code, 200, version_two.text)
        self.assertEqual(version_two.json()["current_version"], 2)
        old_export = self.client.get(
            f"/api/reports/{report_id}/export?format=pdf&version=1",
            headers=headers,
        )
        self.assertEqual(old_export.status_code, 200)
        current_export = self.client.get(
            f"/api/reports/{report_id}/export?format=pdf&version=2",
            headers=headers,
        )
        self.assertEqual(current_export.status_code, 409)

    def test_operator_cannot_create_or_read_report_in_unassigned_plant(self):
        other_org_id, other_factory_id, other_plant_id = self._create_scope("Other")
        operator_id, token = self._create_operator()
        headers = {"Authorization": f"Bearer {token}"}
        denied_source = self._create_report(
            headers,
            source_history_id=self.admin_history_id,
        )
        self.assertEqual(denied_source.status_code, 403)
        denied_create = self._create_report(
            headers,
            plant_id=other_plant_id,
            organization_id=other_org_id,
            factory_id=other_factory_id,
        )
        self.assertEqual(denied_create.status_code, 403)

        created = self._create_report(
            self.admin_headers,
            plant_id=other_plant_id,
            organization_id=other_org_id,
            factory_id=other_factory_id,
            source_history_id=self._create_history(
                1,
                plant_id=other_plant_id,
            ),
        )
        self.assertEqual(created.status_code, 201, created.text)
        denied_read = self.client.get(
            f"/api/reports/{created.json()['id']}",
            headers=headers,
        )
        self.assertEqual(denied_read.status_code, 404)
        with Session(self.engine) as session:
            denial = session.scalar(
                select(UserActionAudit).where(
                    UserActionAudit.actor_user_id == operator_id,
                    UserActionAudit.action == "access.denied",
                    UserActionAudit.object_type == "governed_report",
                    UserActionAudit.object_id == str(created.json()["id"]),
                    UserActionAudit.outcome == "denied",
                )
            )
            self.assertIsNotNone(denial)

    def test_analysis_history_must_match_report_plant_exactly(self):
        matching_history_id = self._create_history(1, plant_id=self.plant_id)
        matching_report = self._create_report(
            self.admin_headers,
            source_history_id=matching_history_id,
        )
        self.assertEqual(matching_report.status_code, 201, matching_report.text)

        _other_org_id, _other_factory_id, other_plant_id = self._create_scope("Mismatch")
        other_history_id = self._create_history(1, plant_id=other_plant_id)
        different_plant = self._create_report(
            self.admin_headers,
            source_history_id=other_history_id,
        )
        self.assertEqual(different_plant.status_code, 422)

        unscoped_history_id = self._create_history(1)
        unscoped_report_source = self._create_report(
            self.admin_headers,
            source_history_id=unscoped_history_id,
        )
        self.assertEqual(unscoped_report_source.status_code, 422)

        unscoped_report = self._create_report(
            self.admin_headers,
            include_plant=False,
            organization_id=self.organization_id,
            factory_id=self.factory_id,
            source_history_id=unscoped_history_id,
        )
        self.assertEqual(unscoped_report.status_code, 201, unscoped_report.text)

    def test_validation_status_is_server_controlled_and_empty_content_cannot_submit(self):
        _operator_id, token = self._create_operator()
        headers = {"Authorization": f"Bearer {token}"}
        request = {
            "title": "Quality report",
            "organization_id": self.organization_id,
            "factory_id": self.factory_id,
            "plant_id": self.plant_id,
            "content": {"title": "Quality report", "summary": "Validated content"},
            "source_data": [{"analysis_history_id": self.operator_history_id}],
        }

        forged_validation = self.client.post(
            "/api/reports",
            headers=headers,
            json={**request, "validation_status": "valid"},
        )
        self.assertEqual(forged_validation.status_code, 422, forged_validation.text)

        with Session(self.engine) as session:
            empty_history = AnalysisHistory(
                analysis_type="hardware_analysis",
                title="Empty source",
                creator_user_id=_operator_id,
                result_data={},
            )
            session.add(empty_history)
            session.commit()
            empty_history_id = empty_history.id
        missing_source_data = self.client.post(
            "/api/reports",
            headers=headers,
            json={
                **request,
                "source_data": [{"analysis_history_id": empty_history_id}],
            },
        )
        self.assertEqual(missing_source_data.status_code, 422)

        empty_report = self.client.post(
            "/api/reports",
            headers=headers,
            json={**request, "content": {"title": "No report body"}},
        )
        self.assertEqual(empty_report.status_code, 201, empty_report.text)
        report_id = empty_report.json()["id"]
        self.assertEqual(
            empty_report.json()["version"]["validation_status"],
            "not_validated",
        )
        submitted = self.client.post(
            f"/api/reports/{report_id}/submit",
            headers=headers,
        )
        self.assertEqual(submitted.status_code, 422, submitted.text)
        self.assertEqual(
            self.client.post(
                f"/api/reports/{report_id}/review",
                headers=self.admin_headers,
                json={"decision": "approved"},
            ).status_code,
            409,
        )

        valid_report = self.client.post(
            "/api/reports",
            headers=headers,
            json=request,
        )
        self.assertEqual(valid_report.status_code, 201, valid_report.text)
        valid_id = valid_report.json()["id"]
        valid_submit = self.client.post(
            f"/api/reports/{valid_id}/submit",
            headers=headers,
        )
        self.assertEqual(valid_submit.status_code, 200, valid_submit.text)
        self.assertEqual(valid_submit.json()["version"]["validation_status"], "valid")
