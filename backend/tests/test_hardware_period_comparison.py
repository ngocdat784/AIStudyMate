import unittest
from datetime import date
from typing import Iterator

import pandas as pd
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models import AnalysisHistory
from app.services.hardware_comparison_service import (
    compare_hardware_periods,
)


class HardwarePeriodComparisonServiceTests(unittest.TestCase):
    def setUp(self):
        self.dataframe = pd.DataFrame(
            {
                "Date": [
                    "2026-10-01",
                    "2026-10-01",
                    "2026-10-02",
                    "2026-11-01",
                    "2026-11-01",
                    "invalid-date",
                ],
                "Supplier": [
                    "Supplier X",
                    "Supplier Y",
                    "Supplier X",
                    "Supplier X",
                    "Supplier Z",
                    "Supplier X",
                ],
                "Model": ["A", "B", "A", "A", "C", "A"],
                "Component": ["Board", "Screen", "Board", "Board", "Screen", "Board"],
                "Batch": ["B1", "B2", "B1", "B3", "B4", "B1"],
                "Production": [100, 50, -5, 200, 10, 100],
                "Defects": [2, 5, 1, 2, 1, 0],
                "Downtime": [10, 5, 1, 20, 1, 1],
            }
        )

    def _compare(self, **overrides):
        arguments = {
            "period_a_start": date(2026, 10, 1),
            "period_a_end": date(2026, 10, 31),
            "period_b_start": date(2026, 11, 1),
            "period_b_end": date(2026, 11, 30),
        }
        arguments.update(overrides)
        return compare_hardware_periods(self.dataframe, **arguments)

    def test_aggregate_comparison_uses_inclusive_ranges_and_metric_rules(self):
        result = self._compare()
        period_a = result["periods"]["period_a"]
        period_b = result["periods"]["period_b"]
        comparison = result["comparisons"][0]

        self.assertEqual(result["comparison_status"], "partial")
        self.assertEqual(period_a["rows_in_period"], 3)
        self.assertEqual(period_a["rows_used_for_kpi"], 2)
        self.assertEqual(period_a["rows_excluded_from_kpi"], 1)
        self.assertEqual(period_a["metrics"]["production"], 150)
        self.assertEqual(period_b["metrics"]["production"], 210)
        self.assertEqual(period_b["metrics"]["defects"], 3)
        self.assertEqual(
            period_a["metrics"]["defect_rate"],
            round(7 / 150 * 100, 2),
        )
        self.assertEqual(
            comparison["differences"]["production"]["absolute"],
            60,
        )
        self.assertEqual(
            comparison["differences"]["production"]["percentage_change"],
            40,
        )
        self.assertEqual(
            comparison["differences"]["defect_rate"]["percentage_points"],
            round(3 / 210 * 100 - 7 / 150 * 100, 2),
        )
        self.assertEqual(result["data_quality"]["invalid_date_rows"], 1)

    def test_dimension_comparison_returns_matched_and_unmatched_groups(self):
        result = self._compare(dimension="Supplier")
        by_supplier = {
            comparison["entity"]: comparison
            for comparison in result["comparisons"]
        }

        self.assertEqual(result["dimension"], "Supplier")
        self.assertEqual(result["comparison_status"], "partial")
        self.assertEqual(by_supplier["Supplier X"]["status"], "partial")
        self.assertEqual(
            by_supplier["Supplier X"]["differences"]["production"]["absolute"],
            100,
        )
        self.assertEqual(
            by_supplier["Supplier Y"]["status"],
            "not_comparable",
        )
        self.assertEqual(
            by_supplier["Supplier Z"]["status"],
            "not_comparable",
        )
        self.assertIsNone(
            by_supplier["Supplier Y"]["differences"]["production"]["absolute"]
        )

    def test_zero_baseline_has_null_percentage_change(self):
        dataframe = pd.DataFrame(
            {
                "Date": ["2026-10-01", "2026-11-01"],
                "Production": [0, 10],
                "Defects": [0, 1],
                "Downtime": [0, 5],
            }
        )
        result = compare_hardware_periods(
            dataframe,
            period_a_start=date(2026, 10, 1),
            period_a_end=date(2026, 10, 31),
            period_b_start=date(2026, 11, 1),
            period_b_end=date(2026, 11, 30),
        )

        self.assertIsNone(
            result["comparisons"][0]["differences"]["production"][
                "percentage_change"
            ]
        )
        self.assertIsNone(
            result["comparisons"][0]["differences"]["defect_rate"][
                "percentage_points"
            ]
        )

    def test_returns_not_comparable_when_period_has_no_valid_kpi_rows(self):
        result = self._compare(
            period_b_start=date(2027, 1, 1),
            period_b_end=date(2027, 1, 31),
        )

        self.assertEqual(result["comparison_status"], "not_comparable")
        self.assertEqual(
            result["periods"]["period_b"]["status"],
            "not_comparable",
        )
        self.assertEqual(result["comparisons"][0]["status"], "not_comparable")

    def test_rejects_invalid_or_overlapping_ranges_and_missing_dimension(self):
        with self.assertRaisesRegex(ValueError, "period_a_start"):
            self._compare(
                period_a_start=date(2026, 11, 1),
                period_a_end=date(2026, 10, 1),
            )
        with self.assertRaisesRegex(ValueError, "không được chồng lấn"):
            self._compare(
                period_b_start=date(2026, 10, 20),
                period_b_end=date(2026, 11, 30),
            )
        with self.assertRaisesRegex(ValueError, "Không tìm thấy cột Supplier"):
            compare_hardware_periods(
                self.dataframe.drop(columns=["Supplier"]),
                period_a_start=date(2026, 10, 1),
                period_a_end=date(2026, 10, 31),
                period_b_start=date(2026, 11, 1),
                period_b_end=date(2026, 11, 30),
                dimension="Supplier",
            )


class HardwarePeriodComparisonEndpointTests(unittest.TestCase):
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

    def test_endpoint_compares_periods_and_saves_history_without_ai(self):
        csv_data = (
            b"Date,Supplier,Production,Defects,Downtime\n"
            b"2026-10-01,Supplier X,100,2,10\n"
            b"2026-11-01,Supplier X,200,4,20\n"
        )
        response = self.client.post(
            "/api/hardware/compare-periods",
            data={
                "period_a_start": "2026-10-01",
                "period_a_end": "2026-10-31",
                "period_b_start": "2026-11-01",
                "period_b_end": "2026-11-30",
                "dimension": "Supplier",
            },
            files={"file": ("periods.csv", csv_data, "text/csv")},
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["comparison_status"], "complete")
        self.assertEqual(result["dimension"], "Supplier")
        self.assertEqual(
            result["comparisons"][0]["differences"]["production"]["absolute"],
            100,
        )
        with Session(self.engine) as session:
            history = session.scalar(select(AnalysisHistory))
            self.assertIsNotNone(history)
            assert history is not None
            self.assertEqual(
                history.analysis_type,
                "hardware_period_comparison",
            )
            self.assertEqual(history.source_filename, "periods.csv")
            self.assertEqual(history.result_data["dimension"], "Supplier")

    def test_endpoint_rejects_overlapping_periods(self):
        response = self.client.post(
            "/api/hardware/compare-periods",
            data={
                "period_a_start": "2026-10-01",
                "period_a_end": "2026-10-31",
                "period_b_start": "2026-10-20",
                "period_b_end": "2026-11-30",
            },
            files={
                "file": (
                    "periods.csv",
                    b"Date,Production\n2026-10-01,100\n",
                    "text/csv",
                )
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("không được chồng lấn", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
