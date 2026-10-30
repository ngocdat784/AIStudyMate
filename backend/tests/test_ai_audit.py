
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from app.services import hardware_ai_service as service


class TestHardwareAIAudit(unittest.TestCase):

    def setUp(self):
        self.report = {
            "total_production": 1000,
            "total_defects": 25,
            "defect_rate": 2.5,
        }

        self.audit_start = patch.object(
            service,
            "start_ai_audit_log",
            return_value=123,
        )
        self.audit_finish = patch.object(
            service,
            "finish_ai_audit_log",
        )

        self.start_mock = self.audit_start.start()
        self.finish_mock = self.audit_finish.start()

        self.addCleanup(self.audit_start.stop)
        self.addCleanup(self.audit_finish.stop)

        self.context_patch = patch.object(
            service,
            "_build_ai_context",
            return_value={"report": self.report},
        )
        self.prompt_patch = patch.object(
            service,
            "_build_prompt",
            return_value="Test hardware analysis prompt",
        )

        self.context_patch.start()
        self.prompt_patch.start()

        self.addCleanup(self.context_patch.stop)
        self.addCleanup(self.prompt_patch.stop)

        # Giữ nguyên bước parse JSON/Pydantic thật.
        # Chỉ mock các bước chuẩn hóa nghiệp vụ để cô lập Audit.
        normalizers = [
            "_validate_risk_areas",
            "_normalize_group_analysis_observations",
            "_normalize_quality_observations",
            "normalize_summary_record_scope",
            "_normalize_aggregate_defect_rate_wording",
            "_normalize_recommendation_priorities",
            "_normalize_risk_levels",
            "enrich_risk_areas",
        ]

        self.normalizer_patches = []

        for name in normalizers:
            mocked = patch.object(
                service,
                name,
                side_effect=lambda value, *args, **kwargs: value,
            )
            mocked.start()
            self.normalizer_patches.append(mocked)
            self.addCleanup(mocked.stop)

    def _valid_response_json(self):
        section = {
            "summary": "Phân tích dựa trên dữ liệu kiểm thử.",
            "key_observations": [],
        }

        return json.dumps({
            "executive_summary": section,
            "production_analysis": section,
            "quality_analysis": section,
            "component_analysis": section,
            "supplier_analysis": section,
            "batch_analysis": section,
            "downtime_analysis": section,
            "risk_areas": [],
            "recommended_actions": [],
        }, ensure_ascii=False)

    def test_audit_success(self):
        raw_response = self._valid_response_json()

        with patch.object(
            service,
            "call_gemini_with_retry",
            return_value=SimpleNamespace(text=raw_response),
        ):
            result = service.analyze_hardware_deep(
                self.report,
                anomalies={},
            )

        self.start_mock.assert_called_once()

        self.finish_mock.assert_called_once()

        audit_args = self.finish_mock.call_args

        self.assertEqual(audit_args.args[0], 123)

        self.assertEqual(
            audit_args.kwargs["status"],
            "success",
        )

        self.assertEqual(
            audit_args.kwargs["validation_status"],
            "passed",
        )

        self.assertEqual(
            audit_args.kwargs["raw_response"],
            raw_response,
        )

        self.assertIsInstance(
            audit_args.kwargs["validated_result"],
            dict,
        )

        self.assertIsNone(audit_args.kwargs["error"])

        self.assertGreaterEqual(
            audit_args.kwargs["duration_ms"],
            0,
        )

        self.assertIsInstance(result, dict)

    def test_audit_gemini_error(self):
        gemini_error = RuntimeError(
            "Gemini connection failed"
        )

        with patch.object(
            service,
            "call_gemini_with_retry",
            side_effect=gemini_error,
        ):
            with self.assertRaises(RuntimeError):
                service.analyze_hardware_deep(
                    self.report,
                    anomalies={},
                )

        self.finish_mock.assert_called_once()

        audit_args = self.finish_mock.call_args

        self.assertEqual(
            audit_args.kwargs["status"],
            "error",
        )

        self.assertEqual(
            audit_args.kwargs["validation_status"],
            "not_run",
        )

        self.assertIsNone(
            audit_args.kwargs["raw_response"],
        )

        self.assertIs(
            audit_args.kwargs["error"],
            gemini_error,
        )

        self.assertIsNone(
            audit_args.kwargs["validated_result"],
        )

    def test_audit_validation_error(self):
        invalid_response = "{}"

        with patch.object(
            service,
            "call_gemini_with_retry",
            return_value=SimpleNamespace(
                text=invalid_response
            ),
        ):
            with self.assertRaises(ValidationError):
                service.analyze_hardware_deep(
                    self.report,
                    anomalies={},
                )

        self.finish_mock.assert_called_once()

        audit_args = self.finish_mock.call_args

        self.assertEqual(
            audit_args.kwargs["status"],
            "validation_failed",
        )

        self.assertEqual(
            audit_args.kwargs["validation_status"],
            "failed",
        )

        self.assertEqual(
            audit_args.kwargs["raw_response"],
            invalid_response,
        )

        self.assertIsInstance(
            audit_args.kwargs["error"],
            ValidationError,
        )

        self.assertIsNone(
            audit_args.kwargs["validated_result"],
        )


if __name__ == "__main__":
    unittest.main()
