import unittest
from datetime import date
from typing import Iterator
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models import AnalysisHistory
from app.services.hardware_intervention_service import (
    evaluate_hardware_intervention,
)
from app.services.multi_file_service import store_merged_hardware_dataset


def _evaluate(dataframe: pd.DataFrame, **overrides):
    arguments = {
        "before_start_date": date(2026, 10, 1),
        "before_end_date": date(2026, 10, 2),
        "after_start_date": date(2026, 10, 3),
        "after_end_date": date(2026, 10, 4),
        **overrides,
    }
    return evaluate_hardware_intervention(dataframe, **arguments)


class HardwareInterventionServiceTests(unittest.TestCase):
    def test_rate_reduction_and_stable_operations_is_improved(self):
        dataframe = pd.DataFrame(
            {
                "Date": [
                    "2026-10-01",
                    "2026-10-02",
                    "2026-10-03",
                    "2026-10-04",
                ],
                "Production": [5000, 5000, 5000, 5000],
                "Defects": [150, 150, 75, 75],
                "Downtime": [10, 10, 10, 10],
            }
        )

        result = _evaluate(dataframe)

        self.assertEqual(result["conclusion"], "improved")
        self.assertEqual(
            result["metrics"]["defect_rate"]["before"],
            3.0,
        )
        self.assertEqual(
            result["metrics"]["defect_rate"]["after"],
            1.5,
        )
        self.assertEqual(
            result["metrics"]["defect_rate"][
                "change_absolute_percentage_points"
            ],
            -1.5,
        )
        self.assertEqual(
            result["metrics"]["defect_rate"]["change_percent"],
            -50.0,
        )
        self.assertEqual(result["ai_status"], "not_requested")

    def test_total_defects_drop_does_not_hide_lower_production(self):
        dataframe = pd.DataFrame(
            {
                "Date": [
                    "2026-10-01",
                    "2026-10-02",
                    "2026-10-03",
                    "2026-10-04",
                ],
                "Production": [5000, 5000, 2500, 2500],
                "Defects": [100, 100, 50, 50],
                "Downtime": [10, 10, 10, 10],
            }
        )

        result = _evaluate(dataframe)

        self.assertEqual(result["metrics"]["defects"]["before"], 200)
        self.assertEqual(result["metrics"]["defects"]["after"], 100)
        self.assertEqual(result["metrics"]["defect_rate"]["before"], 2.0)
        self.assertEqual(result["metrics"]["defect_rate"]["after"], 2.0)
        self.assertEqual(result["conclusion"], "not_improved")
        self.assertEqual(
            result["conclusion_reason"],
            "defect_rate_unchanged",
        )

    def test_production_decline_is_a_mixed_result_not_data_quality_warning(self):
        dataframe = pd.DataFrame(
            {
                "Date": [
                    "2026-10-01",
                    "2026-10-02",
                    "2026-10-03",
                    "2026-10-04",
                ],
                "Production": [5000, 5000, 2500, 2500],
                "Defects": [150, 150, 25, 25],
                "Downtime": [10, 10, 10, 10],
            }
        )

        result = _evaluate(dataframe)

        self.assertEqual(result["metrics"]["defects"]["after"], 50)
        self.assertEqual(result["metrics"]["defect_rate"]["after"], 1.0)
        self.assertEqual(result["conclusion"], "mixed_result")
        self.assertEqual(
            result["conclusion_reason"],
            "quality_improved_with_operational_tradeoff",
        )
        self.assertEqual(result["data_quality"]["status"], "complete")
        self.assertEqual(result["data_quality"]["warnings"], [])
        self.assertEqual(
            result["operational_warnings"],
            ["Sản lượng/ngày giảm 50.00% sau can thiệp."],
        )
        self.assertEqual(result["warnings"], result["operational_warnings"])

    def test_downtime_increase_warning_names_only_the_changed_metric(self):
        dataframe = pd.DataFrame(
            {
                "Date": [
                    "2026-10-01",
                    "2026-10-02",
                    "2026-10-03",
                    "2026-10-04",
                ],
                "Production": [100, 100, 100, 100],
                "Defects": [5, 5, 2, 2],
                "Downtime": [5, 5, 10, 10],
            }
        )

        result = _evaluate(dataframe)

        self.assertEqual(result["conclusion"], "mixed_result")
        self.assertEqual(result["data_quality"]["status"], "complete")
        self.assertEqual(
            result["operational_warnings"],
            ["Downtime/ngày tăng 100.00% sau can thiệp."],
        )

    def test_rate_increase_and_downtime_increase_are_reported(self):
        dataframe = pd.DataFrame(
            {
                "Date": [
                    "2026-10-01",
                    "2026-10-02",
                    "2026-10-03",
                    "2026-10-04",
                ],
                "Production": [100, 100, 100, 100],
                "Defects": [1, 1, 2, 2],
                "Downtime": [5, 5, 10, 10],
            }
        )

        result = _evaluate(dataframe)

        self.assertEqual(result["conclusion"], "not_improved")
        self.assertEqual(
            result["conclusion_reason"],
            "defect_rate_increased",
        )
        self.assertEqual(
            result["metrics"]["downtime"]["per_day"]["change_percent"],
            100.0,
        )

    def test_unequal_period_lengths_compare_per_day_and_keep_totals(self):
        dataframe = pd.DataFrame(
            {
                "Date": [
                    "2026-10-01",
                    "2026-10-02",
                    "2026-10-03",
                    "2026-10-04",
                    "2026-10-05",
                ],
                "Production": [100, 100, 100, 100, 100],
                "Defects": [3, 3, 1, 1, 1],
                "Downtime": [10, 10, 10, 10, 10],
            }
        )

        result = _evaluate(
            dataframe,
            before_start_date=date(2026, 10, 1),
            before_end_date=date(2026, 10, 2),
            after_start_date=date(2026, 10, 3),
            after_end_date=date(2026, 10, 5),
        )

        self.assertEqual(result["metrics"]["defects"]["before"], 6)
        self.assertEqual(result["metrics"]["defects"]["after"], 3)
        self.assertEqual(
            result["metrics"]["defects"]["change_percent"],
            -50.0,
        )
        self.assertEqual(
            result["metrics"]["production"]["per_day"]["before"],
            100,
        )
        self.assertEqual(
            result["metrics"]["production"]["per_day"]["after"],
            100,
        )
        self.assertEqual(result["conclusion"], "improved")

    def test_missing_data_returns_insufficient_data_with_warnings(self):
        dataframe = pd.DataFrame(
            {
                "Date": ["2026-10-01", "2026-10-03"],
                "Production": [100, "invalid"],
                "Defects": [2, 1],
            }
        )

        result = _evaluate(dataframe)

        self.assertEqual(result["conclusion"], "insufficient_data")
        self.assertEqual(result["data_quality"]["status"], "partial")
        self.assertTrue(result["warnings"])
        self.assertIsNone(result["metrics"]["downtime"]["before"])

    def test_partial_metric_coverage_is_not_given_a_success_conclusion(self):
        dataframe = pd.DataFrame(
            {
                "Date": [
                    "2026-10-01",
                    "2026-10-01",
                    "2026-10-03",
                    "2026-10-03",
                ],
                "Production": [100, 100, 100, 100],
                "Defects": [5, None, 2, None],
                "Downtime": [1, 1, 1, 1],
            }
        )

        result = _evaluate(dataframe)

        self.assertEqual(result["conclusion"], "insufficient_data")
        self.assertEqual(
            result["conclusion_reason"],
            "incomplete_data_quality",
        )
        self.assertTrue(result["warnings"])

    def test_rejects_overlapping_ranges_and_missing_requested_model(self):
        dataframe = pd.DataFrame(
            {
                "Date": ["2026-10-01"],
                "Production": [100],
                "Defects": [1],
                "Downtime": [1],
            }
        )

        with self.assertRaisesRegex(ValueError, "kết thúc trước"):
            _evaluate(
                dataframe,
                before_end_date=date(2026, 10, 3),
                after_start_date=date(2026, 10, 3),
            )
        with self.assertRaisesRegex(ValueError, "không có cột Model"):
            _evaluate(dataframe, model="MB-PRO-X1")

    def test_model_filter_limits_comparison_to_selected_model(self):
        dataframe = pd.DataFrame(
            {
                "Date": [
                    "2026-10-01",
                    "2026-10-01",
                    "2026-10-03",
                    "2026-10-03",
                ],
                "Model": ["MB-PRO-X1", "OTHER", "MB-PRO-X1", "OTHER"],
                "Production": [100, 100, 100, 100],
                "Defects": [10, 0, 5, 0],
                "Downtime": [1, 1, 1, 1],
            }
        )

        result = _evaluate(dataframe, model="mb-pro-x1")

        self.assertEqual(result["model"], "mb-pro-x1")
        self.assertEqual(result["metrics"]["defect_rate"]["before"], 10)
        self.assertEqual(result["metrics"]["defect_rate"]["after"], 5)
        self.assertEqual(result["conclusion"], "improved")


class HardwareInterventionEndpointTests(unittest.TestCase):
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

    def test_endpoint_evaluates_and_saves_intervention_history(self):
        dataset_id = store_merged_hardware_dataset(
            pd.DataFrame(
                {
                    "Date": ["2026-10-01", "2026-10-03"],
                    "Model": ["MB-PRO-X1", "MB-PRO-X1"],
                    "Production": [1000, 1000],
                    "Defects": [30, 15],
                    "Downtime": [5, 5],
                }
            )
        )
        request = {
            "dataset_id": dataset_id,
            "before_start_date": "2026-10-01",
            "before_end_date": "2026-10-01",
            "after_start_date": "2026-10-03",
            "after_end_date": "2026-10-03",
            "model": "MB-PRO-X1",
        }
        with patch(
            "app.routes.hardware.explain_hardware_intervention",
            return_value={
                "summary": "Tỷ lệ lỗi đã giảm.",
                "investigation_points": ["Tiếp tục theo dõi."],
            },
        ):
            response = self.client.post(
                "/api/hardware/evaluate-intervention",
                json=request,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["conclusion"], "improved")
        self.assertEqual(payload["ai_status"], "available")
        self.assertEqual(
            payload["metrics"]["defect_rate"][
                "change_absolute_percentage_points"
            ],
            -1.5,
        )
        with Session(self.engine) as session:
            history = session.scalar(select(AnalysisHistory))
            self.assertIsNotNone(history)
            assert history is not None
            self.assertEqual(
                history.analysis_type,
                "hardware_intervention",
            )
            self.assertEqual(history.dataset_id, dataset_id)
            self.assertEqual(
                history.result_data["conclusion"],
                "improved",
            )

        history_response = self.client.get(
            "/api/history",
            params={"analysis_type": "hardware_intervention"},
        )
        self.assertEqual(history_response.status_code, 200)
        self.assertEqual(
            history_response.json()["pagination"]["total"],
            1,
        )

    def test_endpoint_validates_dates_unknown_dataset_and_extra_fields(self):
        valid_dates = {
            "dataset_id": "unknown",
            "before_start_date": "2026-10-01",
            "before_end_date": "2026-10-02",
            "after_start_date": "2026-10-03",
            "after_end_date": "2026-10-04",
        }

        missing_dataset = self.client.post(
            "/api/hardware/evaluate-intervention",
            json=valid_dates,
        )
        invalid_dates = self.client.post(
            "/api/hardware/evaluate-intervention",
            json={
                **valid_dates,
                "dataset_id": store_merged_hardware_dataset(
                    pd.DataFrame(
                        {
                            "Date": ["2026-10-01"],
                            "Production": [1],
                            "Defects": [0],
                            "Downtime": [0],
                        }
                    )
                ),
                "before_end_date": "2026-10-03",
                "after_start_date": "2026-10-03",
            },
        )
        unexpected_field = self.client.post(
            "/api/hardware/evaluate-intervention",
            json={**valid_dates, "unexpected": True},
        )

        self.assertEqual(missing_dataset.status_code, 404)
        self.assertEqual(invalid_dates.status_code, 422)
        self.assertEqual(unexpected_field.status_code, 422)


if __name__ == "__main__":
    unittest.main()
