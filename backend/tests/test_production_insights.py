import json
import unittest
from unittest.mock import Mock, patch

from app.services import production_service


class ProductionInsightsAuditTests(unittest.TestCase):
    def setUp(self):
        self.kpis = {
            "total_production": 100,
            "total_defects": 3,
            "defect_rate": 3.0,
            "total_downtime": 20,
        }
        self.valid_response = {
            "title": "Production analysis",
            "summary": "Sản lượng ghi nhận là 100 sản phẩm.",
            "kpis": [
                {
                    "name": "Tổng sản lượng",
                    "value": 100,
                    "unit": "sản phẩm",
                    "description": "Sản lượng ghi nhận.",
                }
            ],
            "insights": ["Sản lượng ghi nhận là 100 sản phẩm."],
            "warnings": [],
            "recommendations": ["Tiếp tục theo dõi sản lượng."],
        }

    def test_production_insights_success_is_audited(self):
        audit = Mock()
        raw_response = json.dumps(
            self.valid_response,
            ensure_ascii=False,
        )
        client = Mock()
        client.interactions.create.return_value = Mock(
            output_text=raw_response
        )

        with (
            patch.object(
                production_service,
                "AIAuditContext",
                return_value=audit,
            ) as audit_factory,
            patch.object(
                production_service,
                "get_client",
                return_value=client,
            ),
        ):
            result = production_service.generate_ai_insights(self.kpis)

        self.assertEqual(result.model_dump(), self.valid_response)
        audit_arguments = audit_factory.call_args.kwargs
        self.assertEqual(
            audit_arguments["analysis_type"],
            "production_insights",
        )
        self.assertEqual(
            audit_arguments["model_name"],
            production_service.GEMINI_MODEL,
        )
        self.assertEqual(
            audit_arguments["prompt_version"],
            "production_insights_v1",
        )
        self.assertEqual(audit_arguments["input_data"], self.kpis)
        self.assertEqual(
            audit_arguments["prompt_text"],
            client.interactions.create.call_args.kwargs["input"],
        )
        audit.set_raw_response.assert_called_once_with(raw_response)
        audit.succeed.assert_called_once_with(self.valid_response)
        audit.fail.assert_not_called()

    def test_production_insights_api_error_is_audited(self):
        audit = Mock()
        error = RuntimeError("Gemini API unavailable")
        client = Mock()
        client.interactions.create.side_effect = error

        with (
            patch.object(
                production_service,
                "AIAuditContext",
                return_value=audit,
            ),
            patch.object(
                production_service,
                "get_client",
                return_value=client,
            ),
        ):
            with self.assertRaises(RuntimeError) as raised:
                production_service.generate_ai_insights(self.kpis)

        self.assertIs(raised.exception, error)
        audit.fail.assert_called_once_with(error)
        audit.set_raw_response.assert_not_called()
        audit.succeed.assert_not_called()

    def test_production_insights_empty_response_is_audited(self):
        audit = Mock()
        client = Mock()
        client.interactions.create.return_value = Mock(output_text="")

        with (
            patch.object(
                production_service,
                "AIAuditContext",
                return_value=audit,
            ),
            patch.object(
                production_service,
                "get_client",
                return_value=client,
            ),
        ):
            with self.assertRaisesRegex(
                Exception,
                "Gemini không trả về Production Insights",
            ):
                production_service.generate_ai_insights(self.kpis)

        audit.set_raw_response.assert_called_once_with("")
        audit.fail.assert_called_once()
        self.assertTrue(
            audit.fail.call_args.kwargs["validation_failed"]
        )
        audit.succeed.assert_not_called()

    def test_production_insights_invalid_json_is_audited(self):
        audit = Mock()
        raw_response = "This is not valid JSON"
        client = Mock()
        client.interactions.create.return_value = Mock(
            output_text=raw_response
        )

        with (
            patch.object(
                production_service,
                "AIAuditContext",
                return_value=audit,
            ),
            patch.object(
                production_service,
                "get_client",
                return_value=client,
            ),
        ):
            with self.assertRaisesRegex(
                Exception,
                "Gemini trả về Production Insights không hợp lệ",
            ):
                production_service.generate_ai_insights(self.kpis)

        audit.set_raw_response.assert_called_once_with(raw_response)
        audit.fail.assert_called_once()
        self.assertTrue(
            audit.fail.call_args.kwargs["validation_failed"]
        )
        audit.succeed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
