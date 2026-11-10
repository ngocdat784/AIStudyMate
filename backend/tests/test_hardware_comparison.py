import unittest
from unittest.mock import Mock, patch

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from auth_test_helpers import clear_admin_auth_override, override_admin_auth
from app.services import hardware_ai_service
from app.services.hardware_service import compare_hardware_models


class HardwareComparisonTests(unittest.TestCase):
    def setUp(self):
        override_admin_auth()
        self.dataframe = pd.DataFrame(
            {
                "Model": ["A", "B", "B", "A", "B"],
                "Production": [100, 100, 100, 20, 5],
                "Defects": [1.47, 4.39, 4.39, "invalid", -1],
                "Downtime": [4, 8, 8, 1, 2],
            }
        )

    def tearDown(self):
        clear_admin_auth_override()

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

    def test_comparison_ai_audit_records_success_and_raw_response(self):
        comparison = {"models": {"model_a": {"production": 100}}}
        raw_response = (
            '{"summary": "So sánh hoàn tất.", '
            '"investigation_points": ["Đối chiếu chất lượng."]}'
        )
        audit = Mock()
        client = Mock()
        client.models.generate_content.return_value = Mock(text=raw_response)
        with (
            patch.object(
                hardware_ai_service,
                "AIAuditContext",
                return_value=audit,
            ) as audit_context,
            patch.object(hardware_ai_service, "get_client", return_value=client),
            patch.object(
                hardware_ai_service,
                "call_gemini_with_retry",
                side_effect=lambda operation: operation(),
            ),
        ):
            result = hardware_ai_service.analyze_hardware_comparison(
                comparison
            )

        audit_context.assert_called_once()
        audit_kwargs = audit_context.call_args.kwargs
        self.assertEqual(
            audit_kwargs["analysis_type"],
            "hardware_comparison",
        )
        self.assertEqual(
            audit_kwargs["model_name"],
            hardware_ai_service.GEMINI_MODEL,
        )
        self.assertEqual(
            audit_kwargs["prompt_version"],
            "hardware_comparison_v1",
        )
        self.assertEqual(audit_kwargs["input_data"], comparison)
        self.assertEqual(
            audit_kwargs["prompt_text"],
            client.models.generate_content.call_args.kwargs["contents"],
        )
        audit.set_raw_response.assert_called_once_with(raw_response)
        audit.succeed.assert_called_once_with(result)
        self.assertEqual(result["summary"], "So sánh hoàn tất.")

    def test_comparison_ai_error_is_audited_and_reraised(self):
        error = RuntimeError("Gemini unavailable")
        audit = Mock()
        with (
            patch.object(
                hardware_ai_service,
                "AIAuditContext",
                return_value=audit,
            ),
            patch.object(
                hardware_ai_service,
                "call_gemini_with_retry",
                side_effect=error,
            ),
        ):
            with self.assertRaises(RuntimeError) as raised:
                hardware_ai_service.analyze_hardware_comparison({})

        self.assertIs(raised.exception, error)
        audit.fail.assert_called_once_with(error)
        audit.set_raw_response.assert_not_called()
        audit.succeed.assert_not_called()

    def test_comparison_ai_invalid_response_is_audited_as_validation_failure(
        self,
    ):
        raw_response = "invalid JSON"
        audit = Mock()
        with (
            patch.object(
                hardware_ai_service,
                "AIAuditContext",
                return_value=audit,
            ),
            patch.object(
                hardware_ai_service,
                "call_gemini_with_retry",
                return_value=Mock(text=raw_response),
            ),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "Không thể xác thực nhận xét so sánh model",
            ):
                hardware_ai_service.analyze_hardware_comparison({})

        audit.set_raw_response.assert_called_once_with(raw_response)
        audit.fail.assert_called_once()
        self.assertTrue(
            audit.fail.call_args.kwargs["validation_failed"]
        )
        audit.succeed.assert_not_called()

    def test_intervention_explanation_audit_records_success(self):
        intervention_result = {"conclusion": "improved", "metrics": {}}
        raw_response = (
            '{"summary": "Kết quả được cải thiện.", '
            '"investigation_points": ["Tiếp tục theo dõi."]}'
        )
        audit = Mock()
        client = Mock()
        client.models.generate_content.return_value = Mock(text=raw_response)
        with (
            patch.object(
                hardware_ai_service,
                "AIAuditContext",
                return_value=audit,
            ) as audit_context,
            patch.object(hardware_ai_service, "get_client", return_value=client),
            patch.object(
                hardware_ai_service,
                "call_gemini_with_retry",
                side_effect=lambda operation: operation(),
            ),
        ):
            result = hardware_ai_service.explain_hardware_intervention(
                intervention_result
            )

        audit_context.assert_called_once()
        audit_kwargs = audit_context.call_args.kwargs
        self.assertEqual(
            audit_kwargs["analysis_type"],
            "hardware_intervention",
        )
        self.assertEqual(
            audit_kwargs["model_name"],
            hardware_ai_service.GEMINI_MODEL,
        )
        self.assertEqual(
            audit_kwargs["prompt_version"],
            "hardware_intervention_v1",
        )
        self.assertEqual(audit_kwargs["input_data"], intervention_result)
        self.assertEqual(
            audit_kwargs["prompt_text"],
            client.models.generate_content.call_args.kwargs["contents"],
        )
        audit.set_raw_response.assert_called_once_with(raw_response)
        audit.succeed.assert_called_once_with(result)
        self.assertEqual(result["summary"], "Kết quả được cải thiện.")

    def test_intervention_explanation_audits_gemini_error(self):
        audit = Mock()
        error = RuntimeError("Gemini unavailable")
        with (
            patch.object(
                hardware_ai_service,
                "AIAuditContext",
                return_value=audit,
            ),
            patch.object(
                hardware_ai_service,
                "call_gemini_with_retry",
                side_effect=error,
            ),
        ):
            with self.assertRaises(RuntimeError) as raised:
                hardware_ai_service.explain_hardware_intervention({})

        self.assertIs(raised.exception, error)
        audit.fail.assert_called_once_with(error)
        audit.set_raw_response.assert_not_called()
        audit.succeed.assert_not_called()

    def test_intervention_explanation_audits_validation_failure(self):
        raw_response = "invalid JSON"
        audit = Mock()
        with (
            patch.object(
                hardware_ai_service,
                "AIAuditContext",
                return_value=audit,
            ),
            patch.object(
                hardware_ai_service,
                "call_gemini_with_retry",
                return_value=Mock(text=raw_response),
            ),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "Không thể xác thực diễn giải can thiệp từ Gemini",
            ):
                hardware_ai_service.explain_hardware_intervention({})

        audit.set_raw_response.assert_called_once_with(raw_response)
        audit.fail.assert_called_once()
        self.assertTrue(
            audit.fail.call_args.kwargs["validation_failed"]
        )
        audit.succeed.assert_not_called()

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
