import unittest
from datetime import datetime
from typing import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models import AnalysisHistory
from app.services.analysis_history_service import create_analysis_history


class AnalysisHistoryModelTests(unittest.TestCase):
    def test_analysis_history_persists_analysis_payload(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)

        with Session(engine) as session:
            history = AnalysisHistory(
                analysis_type="hardware",
                title="Hardware report",
                summary="Analysis completed.",
                source_filename="production.csv",
                dataset_id="dataset-123",
                result_data={
                    "kpis": [{"name": "Production", "value": 100}],
                    "ai_analysis": {"summary": "Stable."},
                },
            )
            session.add(history)
            session.commit()

            saved = session.scalar(
                select(AnalysisHistory).where(
                    AnalysisHistory.id == history.id
                )
            )

            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertEqual(saved.analysis_type, "hardware")
            self.assertEqual(saved.source_filename, "production.csv")
            self.assertEqual(saved.dataset_id, "dataset-123")
            self.assertEqual(
                saved.result_data["kpis"][0]["value"],
                100,
            )
            self.assertIsInstance(saved.created_at, datetime)

        engine.dispose()


class AnalysisHistoryAPITests(unittest.TestCase):
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
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _create_history(
        self,
        *,
        analysis_type: str = "hardware",
        title: str = "Hardware report",
        result_data: dict | None = None,
    ) -> AnalysisHistory:
        with Session(self.engine) as session:
            return create_analysis_history(
                session,
                analysis_type=analysis_type,
                title=title,
                summary="Analysis completed.",
                source_filename="production.csv",
                dataset_id="dataset-123",
                result_data=result_data or {"kpis": [{"value": 100}]},
            )

    def test_history_list_is_paginated_filtered_and_excludes_result(self):
        first = self._create_history(title="First")
        self._create_history(analysis_type="production", title="Other type")
        latest = self._create_history(title="Latest")

        response = self.client.get(
            "/api/history",
            params={
                "page": 1,
                "page_size": 1,
                "analysis_type": "hardware",
            },
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["pagination"], {
            "page": 1,
            "page_size": 1,
            "total": 2,
            "total_pages": 2,
        })
        self.assertEqual(data["items"][0]["id"], latest.id)
        self.assertNotIn("result_data", data["items"][0])
        self.assertNotEqual(first.id, latest.id)

    def test_history_detail_includes_result_and_missing_id_is_404(self):
        history = self._create_history(
            result_data={"kpis": [{"name": "Production", "value": 100}]}
        )

        response = self.client.get(f"/api/history/{history.id}")
        missing_response = self.client.get("/api/history/999")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["result_data"]["kpis"][0]["value"],
            100,
        )
        self.assertIn("created_at", response.json())
        self.assertEqual(missing_response.status_code, 404)

    def test_delete_removes_only_requested_record(self):
        deleted_history = self._create_history(title="Delete me")
        retained_history = self._create_history(title="Keep me")

        response = self.client.delete(
            f"/api/history/{deleted_history.id}"
        )
        retained_response = self.client.get(
            f"/api/history/{retained_history.id}"
        )
        missing_response = self.client.delete(
            f"/api/history/{deleted_history.id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], deleted_history.id)
        self.assertEqual(retained_response.status_code, 200)
        self.assertEqual(missing_response.status_code, 404)

    def test_history_list_validates_pagination_parameters(self):
        for params in (
            {"page": 0},
            {"page_size": 101},
            {"analysis_type": ""},
        ):
            with self.subTest(params=params):
                response = self.client.get("/api/history", params=params)
                self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
