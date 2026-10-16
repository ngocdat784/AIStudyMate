import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from app.services.hardware_service import compare_hardware_models


class HardwareComparisonTests(unittest.TestCase):
    def setUp(self):
        self.dataframe = pd.DataFrame(
            {
                "Model": ["A", "B", "B", "A", "B"],
                "Production": [100, 100, 100, 20, 5],
                "Defects": [1.47, 4.39, 4.39, "invalid", -1],
                "Downtime": [4, 8, 8, 1, 2],
            }
        )

    def test_comparison_uses_shared_cleaning_and_kpi_rules(self):
        result = compare_hardware_models(
            self.dataframe,
            "A",
            "B",
        )

        self.assertEqual(result["models"]["model_a"]["production"], 100)
        self.assertEqual(result["models"]["model_a"]["defect_rate"], 1.47)
        self.assertEqual(result["models"]["model_b"]["production"], 100)
        self.assertEqual(result["models"]["model_b"]["defects"], 4.39)
        self.assertEqual(result["models"]["model_b"]["rows_used_for_kpi"], 1)
        self.assertEqual(result["models"]["model_b"]["invalid_rows_excluded"], 1)
        self.assertEqual(
            result["differences"]["defect_rate_percentage_points"],
            2.92,
        )
        self.assertEqual(result["higher_defect_rate_model"], "B")

    def test_rejects_identical_or_unknown_models(self):
        with self.assertRaisesRegex(ValueError, "phải khác nhau"):
            compare_hardware_models(self.dataframe, "A", "A")

        with self.assertRaisesRegex(ValueError, "Không tìm thấy model"):
            compare_hardware_models(self.dataframe, "A", "missing")

    def test_zero_production_makes_defect_rate_comparison_unavailable(self):
        dataframe = pd.DataFrame(
            {
                "Model": ["A", "B"],
                "Production": [0, 100],
                "Defects": [2, 1],
                "Downtime": [0, 1],
            }
        )

        result = compare_hardware_models(dataframe, "A", "B")

        self.assertIsNone(result["models"]["model_a"]["defect_rate"])
        self.assertIsNone(
            result["differences"]["defect_rate_percentage_points"]
        )
        self.assertEqual(
            result["rate_comparison_status"],
            "insufficient_production",
        )
        self.assertIsNone(result["higher_defect_rate_model"])

    def test_rates_match_unrounded_shared_kpi_calculation(self):
        dataframe = pd.DataFrame(
            {
                "Model": ["A", "B"],
                "Production": [2.345, 1.23],
                "Defects": [1.234, 0.5678],
                "Downtime": [1, 1],
            }
        )

        result = compare_hardware_models(dataframe, "A", "B")

        self.assertEqual(result["models"]["model_a"]["defect_rate"], 52.62)
        self.assertEqual(result["models"]["model_b"]["defect_rate"], 46.16)
        self.assertEqual(
            result["differences"]["defect_rate_percentage_points"],
            -6.46,
        )

    def test_rejects_model_with_no_valid_metric_rows(self):
        dataframe = pd.DataFrame(
            {
                "Model": ["A", "B"],
                "Production": [100, 100],
                "Defects": [-1, 1],
                "Downtime": [1, 1],
            }
        )

        with self.assertRaisesRegex(ValueError, "không có đủ dữ liệu hợp lệ"):
            compare_hardware_models(dataframe, "A", "B")

    def test_compare_endpoint_returns_python_metrics_and_ai_analysis(self):
        client = TestClient(app)
        csv_content = (
            b"Model,Production,Defects,Downtime\n"
            b"A,100,1.47,4\n"
            b"B,100,4.39,8\n"
        )
        ai_result = {
            "summary": "Model B cần được ưu tiên kiểm tra.",
            "investigation_points": ["Kiểm tra quy trình kiểm soát chất lượng."],
        }

        with patch(
            "app.routes.hardware.analyze_hardware_comparison",
            return_value=ai_result,
        ) as analyze_ai:
            response = client.post(
                "/api/hardware/compare",
                data={"model_a": "A", "model_b": "B"},
                files={"file": ("models.csv", csv_content, "text/csv")},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["differences"]["defect_rate_percentage_points"],
            2.92,
        )
        self.assertEqual(payload["ai_analysis"], ai_result)
        self.assertEqual(payload["ai_status"], "available")
        analyze_ai.assert_called_once()

    def test_compare_endpoint_reports_identical_models_as_client_error(self):
        client = TestClient(app)
        csv_content = b"Model,Production,Defects,Downtime\nA,100,1,4\n"

        response = client.post(
            "/api/hardware/compare",
            data={"model_a": "A", "model_b": "A"},
            files={"file": ("models.csv", csv_content, "text/csv")},
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("phải khác nhau", response.json()["detail"])

    def test_ai_failure_does_not_hide_python_comparison_results(self):
        client = TestClient(app)
        csv_content = (
            b"Model,Production,Defects,Downtime\n"
            b"A,100,1,4\n"
            b"B,100,3,8\n"
        )

        with patch(
            "app.routes.hardware.analyze_hardware_comparison",
            side_effect=RuntimeError("AI unavailable"),
        ):
            response = client.post(
                "/api/hardware/compare",
                data={"model_a": "A", "model_b": "B"},
                files={"file": ("models.csv", csv_content, "text/csv")},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["ai_status"], "unavailable")
        self.assertIsNone(payload["ai_analysis"])
        self.assertIn("Python", payload["ai_error"])
        self.assertEqual(
            payload["differences"]["defect_rate_percentage_points"],
            2.0,
        )


if __name__ == "__main__":
    unittest.main()
