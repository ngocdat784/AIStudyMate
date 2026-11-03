import unittest
from unittest.mock import patch


class TestAIAuditContext(unittest.TestCase):
    def setUp(self):
        self.start_patcher = patch(
            "app.services.ai_audit_context.start_ai_audit_log",
            return_value=123,
        )
        self.finish_patcher = patch(
            "app.services.ai_audit_context.finish_ai_audit_log",
            return_value=True,
        )

        self.mock_start = self.start_patcher.start()
        self.mock_finish = self.finish_patcher.start()

        from app.services.ai_audit_context import AIAuditContext

        self.AIAuditContext = AIAuditContext

    def tearDown(self):
        self.start_patcher.stop()
        self.finish_patcher.stop()

    def create_context(self):
        return self.AIAuditContext(
            analysis_type="hardware_insights",
            model_name="gemini-3.5-flash-lite",
            prompt_version="hardware_insights_v1",
            input_data={"kpis": {"production": 100}},
            prompt_text="Test prompt",
        )

    def test_init_creates_audit_record(self):
        context = self.create_context()

        self.assertEqual(context.audit_id, 123)
        self.assertFalse(context.finalized)

        self.mock_start.assert_called_once_with(
            analysis_type="hardware_insights",
            model_name="gemini-3.5-flash-lite",
            prompt_version="hardware_insights_v1",
            input_data={"kpis": {"production": 100}},
            prompt_text="Test prompt",
        )

    def test_set_raw_response(self):
        context = self.create_context()

        raw_response = '{"summary": "Analysis completed"}'
        context.set_raw_response(raw_response)

        self.assertEqual(context.raw_response, raw_response)

    def test_succeed_finalizes_audit_successfully(self):
        context = self.create_context()

        raw_response = '{"summary": "Analysis completed"}'
        result = {"summary": "Analysis completed"}

        context.set_raw_response(raw_response)

        success = context.succeed(result)

        self.assertTrue(success)
        self.assertTrue(context.finalized)

        args = self.mock_finish.call_args

        self.assertEqual(args.args[0], 123)
        self.assertEqual(args.kwargs["status"], "success")
        self.assertEqual(args.kwargs["validation_status"], "passed")
        self.assertEqual(args.kwargs["raw_response"], raw_response)
        self.assertEqual(args.kwargs["validated_result"], result)
        self.assertGreaterEqual(args.kwargs["duration_ms"], 0)
        self.assertIsNone(args.kwargs["error"])

    def test_fail_records_ai_error(self):
        context = self.create_context()

        error = RuntimeError("Gemini API unavailable")

        success = context.fail(error)

        self.assertTrue(success)
        self.assertTrue(context.finalized)

        args = self.mock_finish.call_args

        self.assertEqual(args.kwargs["status"], "error")
        self.assertEqual(args.kwargs["validation_status"], "not_run")
        self.assertIs(args.kwargs["error"], error)

    def test_fail_records_validation_error(self):
        context = self.create_context()

        error = ValueError("Invalid AI response")

        success = context.fail(
            error,
            validation_failed=True,
        )

        self.assertTrue(success)
        self.assertTrue(context.finalized)

        args = self.mock_finish.call_args

        self.assertEqual(
            args.kwargs["status"],
            "validation_failed",
        )
        self.assertEqual(
            args.kwargs["validation_status"],
            "failed",
        )
        self.assertIs(args.kwargs["error"], error)

    def test_finalize_only_runs_once(self):
        context = self.create_context()

        first_result = context.succeed(
            {"summary": "First result"}
        )

        second_result = context.fail(
            RuntimeError("Second error")
        )

        self.assertTrue(first_result)
        self.assertFalse(second_result)

        self.mock_finish.assert_called_once()

    def test_audit_failure_does_not_escape(self):
        self.mock_finish.side_effect = RuntimeError(
            "Database connection failed"
        )

        context = self.create_context()

        result = context.succeed(
            {"summary": "Analysis completed"}
        )

        self.assertFalse(result)
        self.assertTrue(context.finalized)

        self.mock_finish.assert_called_once()


if __name__ == "__main__":
    unittest.main()
