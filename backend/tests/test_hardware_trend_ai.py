import json
import unittest
from unittest.mock import Mock, patch

import pandas as pd
from pydantic import ValidationError
from fastapi.testclient import TestClient

from app.main import app
from app.services.hardware_ai_service import (
    HardwareTrendAnalysis,
    _validate_hardware_trend_analysis,
    analyze_hardware_trends_with_ai,
)
from app.services.hardware_trend_service import analyze_hardware_trends
from app.services.multi_file_service import store_merged_hardware_dataset


def _trend_report():
    return analyze_hardware_trends(
        pd.DataFrame(
            {
                "Date": ["2026-10-01", "2026-10-02"],
                "Production": [100, 60],
                "Defects": [1, 3],
                "Downtime": [2, 4],
            }
        ),
        granularity="day",
    )


def _valid_ai_result():
    return {
        "summary": "Sản lượng biến động và chất lượng cần được theo dõi.",
        "summary_evidence_ids": ["trend_production"],
        "trend_interpretation": [
            {
                "metric": "production",
                "direction": "decreasing",
                "observation": "Sản lượng giảm đáng kể so với kỳ quan sát trước.",
                "evidence_ids": ["trend_production"],
            }
        ],
        "risk_areas": [
            {
                "area": "production_stability",
                "priority": "high",
                "reason": "Có cảnh báo giảm sản lượng cần được xác minh.",
                "evidence_ids": ["anomaly_0"],
            }
        ],
        "recommended_actions": [
            {
                "action": "Đối chiếu lịch sản xuất và nhật ký vận hành.",
                "purpose": "Xác minh các yếu tố liên quan đến biến động.",
                "priority": "high",
                "evidence_ids": ["anomaly_0"],
            }
        ],
        "limitations": [
            "Dữ liệu tổng hợp không đủ để xác nhận nguyên nhân.",
        ],
    }


class HardwareTrendAIServiceTests(unittest.TestCase):
    def test_analysis_uses_python_evidence_and_validates_ai_response(self):
        report = _trend_report()
        response = Mock(
            text=json.dumps(_valid_ai_result(), ensure_ascii=False)
        )
        client = Mock()
        client.models.generate_content.return_value = response

        with (
            patch(
                "app.services.hardware_ai_service.get_client",
                return_value=client,
            ),
            patch(
                "app.services.hardware_ai_service.call_gemini_with_retry",
                side_effect=lambda operation: operation(),
            ),
            patch(
                "app.services.hardware_ai_service.logger.info"
            ) as log_response,
        ):
            result = analyze_hardware_trends_with_ai(report)

        self.assertEqual(result["title"], "Phân tích xu hướng sản xuất")
        self.assertEqual(result["summary"], _valid_ai_result()["summary"])
        self.assertIn("anomaly_0", result["evidence"])
        self.assertEqual(
            result["evidence"]["anomaly_0"]["evidence_kind"],
            "anomaly",
        )
        request = client.models.generate_content.call_args.kwargs
        self.assertEqual(
            request["config"]["response_mime_type"],
            "application/json",
        )
        self.assertNotIn("response_schema", request["config"])
        self.assertIn("response_json_schema", request["config"])
        self.assertIn("$defs", request["config"]["response_json_schema"])
        self.assertIn("Không tính lại", request["contents"])
        self.assertIn("evidence", request["contents"])
        self.assertIn("summary_evidence_ids", request["contents"])
        self.assertIn("trend_interpretation", request["contents"])
        self.assertIn("recommended_actions", request["contents"])
        self.assertIn("QUY TẮC ĐỊNH DẠNG JSON BẮT BUỘC", request["contents"])
        self.assertIn("không được bỏ observation", request["contents"])
        self.assertIn("Không dùng khóa type hoặc description", request["contents"])
        self.assertIn("không trả về chuỗi đơn thuần", request["contents"])
        self.assertNotIn("bao_cao_phan_tich", _valid_ai_result())
        log_response.assert_called_once_with(
            "Gemini hardware trend response: %s",
            response.text,
        )

    def test_gemini_schema_omits_unsupported_constraints(self):
        response = Mock(
            text=json.dumps(_valid_ai_result(), ensure_ascii=False)
        )
        client = Mock()
        client.models.generate_content.return_value = response

        with (
            patch(
                "app.services.hardware_ai_service.get_client",
                return_value=client,
            ),
            patch(
                "app.services.hardware_ai_service.call_gemini_with_retry",
                side_effect=lambda operation: operation(),
            ),
        ):
            analyze_hardware_trends_with_ai(_trend_report())

        config = client.models.generate_content.call_args.kwargs["config"]
        self.assertEqual(config["response_mime_type"], "application/json")
        self.assertNotIn("response_schema", config)
        schema = config["response_json_schema"]

        def find_unsupported_constraints(value):
            if isinstance(value, dict):
                if any(
                    key in {"additionalProperties", "minLength", "maxLength"}
                    for key in value
                ):
                    return True
                return any(
                    find_unsupported_constraints(item)
                    for item in value.values()
                )
            if isinstance(value, list):
                return any(
                    find_unsupported_constraints(item)
                    for item in value
                )
            return False

        self.assertFalse(find_unsupported_constraints(schema))
        self.assertIn("$defs", schema)
        self.assertIn("$ref", str(schema))

    def test_analysis_rejects_unknown_evidence_references(self):
        report = _trend_report()
        invalid_result = _valid_ai_result()
        invalid_result["summary_evidence_ids"] = ["missing_summary"]
        invalid_result["trend_interpretation"][0]["evidence_ids"] = [
            "missing_trend"
        ]
        invalid_result["risk_areas"][0]["evidence_ids"] = ["missing_risk"]
        invalid_result["recommended_actions"][0]["evidence_ids"] = [
            "missing_action"
        ]

        with (
            patch(
                "app.services.hardware_ai_service.call_gemini_with_retry",
                return_value=Mock(
                    text=json.dumps(invalid_result, ensure_ascii=False)
                ),
            ),
            patch(
                "app.services.hardware_ai_service.logger.exception"
            ) as log_exception,
            patch(
                "app.services.hardware_ai_service.logger.error"
            ) as log_error,
        ):
            with self.assertRaisesRegex(ValueError, "AI tham chiếu evidence"):
                analyze_hardware_trends_with_ai(report)

        error_message = str(log_exception.call_args.args)
        for evidence_id in (
            "missing_summary",
            "missing_trend",
            "missing_risk",
            "missing_action",
        ):
            self.assertIn(evidence_id, error_message)
        log_exception.assert_called_once()
        self.assertIn(
            invalid_result["summary"],
            log_exception.call_args.args[1],
        )
        log_error.assert_called_once()
        self.assertIn(
            "HardwareTrendAnalysis",
            log_error.call_args.args[1],
        )

    def test_analysis_logs_and_reraises_pydantic_validation_error(self):
        invalid_result = _valid_ai_result()
        del invalid_result["trend_interpretation"][0]["observation"]
        response_text = json.dumps(invalid_result, ensure_ascii=False)

        with (
            patch(
                "app.services.hardware_ai_service.call_gemini_with_retry",
                return_value=Mock(text=response_text),
            ),
            patch(
                "app.services.hardware_ai_service.logger.exception"
            ) as log_exception,
            patch(
                "app.services.hardware_ai_service.logger.error"
            ) as log_error,
        ):
            with self.assertRaisesRegex(ValidationError, "observation"):
                analyze_hardware_trends_with_ai(_trend_report())

        log_exception.assert_called_once()
        self.assertIn(
            response_text,
            log_exception.call_args.args[1],
        )
        log_error.assert_called_once()

    def test_analysis_rejects_unverified_numbers_and_periods_in_narrative(self):
        analysis = HardwareTrendAnalysis.model_validate(_valid_ai_result())
        analysis.summary = "Sản lượng giảm vào ngày 2026-10-02."
        evidence = {
            "trend_production": {
                "type": "overall_trend",
            },
            "anomaly_0": {
                "type": "anomaly",
            },
        }

        with self.assertRaisesRegex(ValueError, "số liệu hoặc kỳ"):
            _validate_hardware_trend_analysis(
                analysis,
                evidence,
                _trend_report(),
            )

    def test_analysis_rejects_direction_that_conflicts_with_python(self):
        analysis = HardwareTrendAnalysis.model_validate(_valid_ai_result())
        analysis.trend_interpretation[0].direction = "increasing"
        evidence = {
            "trend_production": {"type": "overall_trend"},
            "anomaly_0": {"type": "anomaly"},
        }

        with self.assertRaisesRegex(ValueError, "không khớp kết quả Python"):
            _validate_hardware_trend_analysis(
                analysis,
                evidence,
                _trend_report(),
            )


class HardwareTrendAIEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_ai_unavailability_keeps_python_trend_statistics(self):
        dataset_id = store_merged_hardware_dataset(
            pd.DataFrame(
                {
                    "Date": ["2026-10-01"],
                    "Production": [100],
                    "Defects": [2],
                }
            )
        )

        api_error = RuntimeError("Gemini rejected request")
        api_error.message = "Request contains an invalid argument."
        with (
            patch(
                "app.routes.hardware.analyze_hardware_trends_with_ai",
                side_effect=api_error,
            ),
            patch("app.routes.hardware.logger.exception") as log_exception,
            patch("app.routes.hardware.logger.error") as log_error,
        ):
            response = self.client.post(
                "/api/hardware/trends",
                json={
                    "dataset_id": dataset_id,
                    "granularity": "day",
                },
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["ai_status"], "unavailable")
        self.assertIsNone(result["ai_analysis"])
        self.assertIn("Python", result["ai_error"])
        self.assertEqual(result["summary"]["totals"]["production"], 100)
        self.assertEqual(result["summary"]["totals"]["defect_rate"], 2)
        log_exception.assert_called_once_with(
            "Hardware trend AI analysis failed: %s",
            str(api_error),
        )
        log_error.assert_called_once_with(
            "Gemini API error detail: %s",
            api_error.message,
        )


if __name__ == "__main__":
    unittest.main()
