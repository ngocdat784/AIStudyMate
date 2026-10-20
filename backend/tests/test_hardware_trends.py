import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from app.services.hardware_trend_service import analyze_hardware_trends
from app.services.multi_file_service import (
    get_merged_hardware_dataset,
    store_merged_hardware_dataset,
)


class HardwareTrendServiceTests(unittest.TestCase):
    def test_daily_totals_changes_and_rate_points(self):
        dataframe = pd.DataFrame(
            {
                "Date": [
                    "2026-10-01",
                    "2026-10-02",
                    "2026-10-03",
                    "2026-10-04",
                    "2026-10-05",
                ],
                "Production": [2200, 2800, 2000, 2650, 3350],
                "Defects": [45, 48, 37, 48, 53],
                "Downtime": [10, 12, 11, 14, 15],
            }
        )

        result = analyze_hardware_trends(dataframe, "day")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["summary"]["periods_count"], 5)
        self.assertEqual(result["summary"]["totals"]["production"], 13000)
        self.assertEqual(result["summary"]["totals"]["defects"], 231)
        self.assertEqual(
            result["time_series"][0]["defect_rate"],
            round(45 / 2200 * 100, 2),
        )
        self.assertEqual(result["period_changes"][1]["production_delta"], -800)
        self.assertEqual(
            result["period_changes"][1]["production_change_percent"],
            -28.57,
        )
        self.assertEqual(
            result["period_changes"][1][
                "defect_rate_delta_percentage_points"
            ],
            round(37 / 2000 * 100 - 48 / 2800 * 100, 2),
        )
        self.assertTrue(
            any(
                anomaly["type"] == "production_drop"
                and anomaly["period_start"] == "2026-10-03"
                for anomaly in result["anomalies"]
            )
        )

    def test_week_granularity_uses_iso_monday_and_does_not_fill_gaps(self):
        dataframe = pd.DataFrame(
            {
                "Date": ["2026-10-04", "2026-10-05", "2026-10-20"],
                "Production": [10, 20, 30],
                "Defects": [1, 2, 3],
                "Downtime": [1, 2, 3],
            }
        )

        result = analyze_hardware_trends(dataframe, "week")

        self.assertEqual(
            result["time_series"][0]["period_start"],
            "2026-09-28",
        )
        self.assertEqual(
            result["time_series"][1]["period_start"],
            "2026-10-05",
        )
        self.assertEqual(
            result["time_series"][0]["period_end"],
            "2026-10-04",
        )
        self.assertEqual(
            result["period_changes"][1]["missing_periods_between"],
            1,
        )
        self.assertTrue(result["warnings"])
        self.assertEqual(result["week_definition"], "ISO 8601, bắt đầu thứ Hai")

    def test_month_granularity_and_inclusive_date_filter(self):
        dataframe = pd.DataFrame(
            {
                "Date": ["2026-09-30", "2026-10-01", "2026-10-31", "2026-11-01"],
                "Production": [1, 2, 3, 4],
                "Defects": [0, 1, 1, 2],
                "Downtime": [0, 2, 3, 4],
            }
        )

        result = analyze_hardware_trends(
            dataframe,
            "month",
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 31),
        )

        self.assertEqual(len(result["time_series"]), 1)
        self.assertEqual(result["time_series"][0]["period_start"], "2026-10-01")
        self.assertEqual(result["time_series"][0]["period_end"], "2026-10-31")
        self.assertEqual(result["summary"]["rows_in_range"], 2)
        self.assertEqual(result["summary"]["rows_outside_requested_range"], 2)
        self.assertEqual(result["summary"]["totals"]["production"], 5)

    def test_invalid_dates_and_metric_values_are_reported_not_coerced(self):
        dataframe = pd.DataFrame(
            {
                "Date": ["2026-10-01", "2026-10-02", "ambiguous", None],
                "Production": ["1,200", "1,2", 30, 40],
                "Defects": [12, 1, 2, None],
                "Downtime": [5, -1, 2, None],
            }
        )

        result = analyze_hardware_trends(dataframe, "day")

        self.assertEqual(result["summary"]["invalid_date_rows"], 1)
        self.assertEqual(result["summary"]["missing_date_rows"], 1)
        self.assertEqual(
            result["summary"]["invalid_numeric_values"]["production"],
            1,
        )
        self.assertEqual(
            result["summary"]["invalid_numeric_values"]["downtime"],
            1,
        )
        self.assertEqual(result["summary"]["totals"]["production"], 1200)
        self.assertEqual(result["summary"]["rows_used"], 1)
        self.assertEqual(result["summary"]["rows_excluded"], 1)
        self.assertEqual(result["time_series"][0]["rows_excluded"], 0)
        self.assertIsNone(result["time_series"][1]["defect_rate"])
        self.assertTrue(result["warnings"])

    def test_empty_periods_are_not_zero_filled_and_zero_baseline_percent_is_null(self):
        dataframe = pd.DataFrame(
            {
                "Date": ["2026-10-01", "2026-10-03"],
                "Production": [0, 100],
                "Defects": [0, 1],
                "Downtime": [0, 10],
            }
        )

        result = analyze_hardware_trends(dataframe, "day")

        self.assertEqual(len(result["time_series"]), 2)
        self.assertIsNone(
            result["period_changes"][0]["production_change_percent"]
        )
        self.assertEqual(
            result["period_changes"][0]["missing_periods_between"],
            1,
        )
        self.assertEqual(result["summary"]["totals"]["defect_rate"], 1.0)

    def test_rejects_reversed_date_range(self):
        dataframe = pd.DataFrame(
            {
                "Date": ["2026-10-01"],
                "Production": [1],
            }
        )

        with self.assertRaisesRegex(ValueError, "start_date"):
            analyze_hardware_trends(
                dataframe,
                "day",
                start_date=date(2026, 10, 2),
                end_date=date(2026, 10, 1),
            )


class HardwareTrendEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_multi_file_endpoint_provides_dataset_id_for_trend_endpoint(self):
        merge_response = self.client.post(
            "/api/hardware/analyze-multiple",
            files=[
                (
                    "files",
                    (
                        "october.csv",
                        (
                            b"Date,Production,Defects,Downtime\n"
                            b"2026-10-01,100,2,5\n"
                            b"2026-10-02,200,6,10\n"
                        ),
                        "text/csv",
                    ),
                ),
                (
                    "files",
                    (
                        "november.csv",
                        (
                            b"Date,Production,Defects,Downtime\n"
                            b"2026-11-01,300,3,8\n"
                        ),
                        "text/csv",
                    ),
                ),
            ],
        )

        self.assertEqual(merge_response.status_code, 200)
        dataset_id = merge_response.json()["dataset_id"]
        self.assertIsInstance(dataset_id, str)

        with patch(
            "app.routes.hardware.analyze_hardware_trends_with_ai",
            return_value={"summary": "Xu hướng được phân tích."},
        ):
            trend_response = self.client.post(
                "/api/hardware/trends",
                json={
                    "dataset_id": dataset_id,
                    "granularity": "month",
                    "start_date": "2026-10-01",
                    "end_date": "2026-10-31",
                },
            )

        self.assertEqual(trend_response.status_code, 200)
        result = trend_response.json()
        self.assertEqual(result["granularity"], "month")
        self.assertEqual(result["summary"]["periods_count"], 1)
        self.assertEqual(result["summary"]["totals"]["production"], 300)
        self.assertEqual(
            result["requested_range"]["start_date"],
            "2026-10-01",
        )

    def test_trend_endpoint_returns_404_for_unknown_dataset(self):
        response = self.client.post(
            "/api/hardware/trends",
            json={
                "dataset_id": "unknown-dataset",
                "granularity": "day",
            },
        )

        self.assertEqual(response.status_code, 404)

    def test_trend_endpoint_rejects_unknown_granularity(self):
        dataset_id = store_merged_hardware_dataset(
            pd.DataFrame(
                {
                    "Date": ["2026-10-01"],
                    "Production": [1],
                }
            )
        )
        self.assertIsNotNone(get_merged_hardware_dataset(dataset_id))

        response = self.client.post(
            "/api/hardware/trends",
            json={
                "dataset_id": dataset_id,
                "granularity": "quarter",
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_cached_dataset_expires(self):
        with patch(
            "app.services.multi_file_service.DATASET_CACHE_TTL_SECONDS",
            0,
        ):
            dataset_id = store_merged_hardware_dataset(
                pd.DataFrame(
                    {
                        "Date": ["2026-10-01"],
                        "Production": [1],
                    }
                )
            )
            self.assertIsNone(get_merged_hardware_dataset(dataset_id))


if __name__ == "__main__":
    unittest.main()
