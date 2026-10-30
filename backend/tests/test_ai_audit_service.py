
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services import ai_audit_service as audit_service


class TestAIAuditService(unittest.TestCase):

    def setUp(self):
        self.db = Mock()
        self.db.add = Mock()
        self.db.commit = Mock()
        self.db.refresh = Mock()
        self.db.rollback = Mock()
        self.db.close = Mock()
        self.db.scalar = Mock()

    # --------------------------------------------------
    # START AUDIT
    # --------------------------------------------------

    @patch.object(audit_service, "SessionLocal")
    def test_start_audit_success(self, session_local):
        session_local.return_value = self.db

        result = audit_service.start_ai_audit_log(
            analysis_type="hardware_analysis",
            model_name="gemini-test",
            prompt_version="hardware_deep_v1",
            input_data={"production": 100},
            prompt_text="Test prompt",
        )
        self.db.refresh.side_effect = lambda record: setattr(record, "id", 123)

        self.assertEqual(result, self.db.add.call_args.args[0].id)
        self.db.add.assert_called_once()
        self.db.commit.assert_called_once()
        self.db.refresh.assert_called_once()
        self.db.close.assert_called_once()

        record = self.db.add.call_args.args[0]

        self.assertEqual(record.analysis_type, "hardware_analysis")
        self.assertEqual(record.model_name, "gemini-test")
        self.assertEqual(record.prompt_version, "hardware_deep_v1")
        self.assertEqual(record.input_data, {"production": 100})
        self.assertEqual(record.prompt_text, "Test prompt")
        self.assertEqual(record.status, "started")
        self.assertEqual(record.validation_status, "not_run")

    @patch.object(audit_service, "SessionLocal")
    def test_start_audit_database_operation_error(self, session_local):
        session_local.return_value = self.db
        self.db.commit.side_effect = RuntimeError(
            "Database commit failed"
        )

        result = audit_service.start_ai_audit_log(
            analysis_type="hardware_analysis",
            model_name="gemini-test",
            prompt_version="v1",
        )

        self.assertIsNone(result)
        self.db.rollback.assert_called_once()
        self.db.close.assert_called_once()

    @patch.object(audit_service, "SessionLocal")
    def test_start_audit_session_creation_error(self, session_local):
        session_local.side_effect = RuntimeError(
            "Database unavailable"
        )

        result = audit_service.start_ai_audit_log(
            analysis_type="hardware_analysis",
            model_name="gemini-test",
            prompt_version="v1",
        )

        self.assertIsNone(result)

    # --------------------------------------------------
    # FINISH AUDIT
    # --------------------------------------------------

    @patch.object(audit_service, "SessionLocal")
    def test_finish_audit_success(self, session_local):
        session_local.return_value = self.db

        record = SimpleNamespace(
            id=123,
            status="started",
            validation_status="not_run",
            raw_response=None,
            validated_result=None,
            error_type=None,
            error_message=None,
            duration_ms=None,
            completed_at=None,
        )

        self.db.scalar.return_value = record

        result = audit_service.finish_ai_audit_log(
            123,
            status="success",
            validation_status="passed",
            raw_response='{"result": "ok"}',
            validated_result={"result": "ok"},
            duration_ms=250,
        )

        self.assertTrue(result)
        self.assertEqual(record.status, "success")
        self.assertEqual(record.validation_status, "passed")
        self.assertEqual(record.raw_response, '{"result": "ok"}')
        self.assertEqual(record.validated_result, {"result": "ok"})
        self.assertEqual(record.duration_ms, 250)
        self.assertIsNotNone(record.completed_at)
        self.assertIsNone(record.error_type)
        self.assertIsNone(record.error_message)

        self.db.commit.assert_called_once()
        self.db.close.assert_called_once()

    @patch.object(audit_service, "SessionLocal")
    def test_finish_audit_with_error(self, session_local):
        session_local.return_value = self.db

        record = SimpleNamespace(
            id=123,
            status="started",
            validation_status="not_run",
            raw_response=None,
            validated_result=None,
            error_type=None,
            error_message=None,
            duration_ms=None,
            completed_at=None,
        )

        self.db.scalar.return_value = record

        original_error = ValueError("Invalid AI response")

        result = audit_service.finish_ai_audit_log(
            123,
            status="validation_failed",
            validation_status="failed",
            raw_response="invalid json",
            error=original_error,
            duration_ms=100,
        )

        self.assertTrue(result)
        self.assertEqual(record.status, "validation_failed")
        self.assertEqual(record.validation_status, "failed")
        self.assertEqual(record.error_type, "ValueError")
        self.assertEqual(record.error_message, "Invalid AI response")
        self.assertEqual(record.raw_response, "invalid json")
        self.assertIsNone(record.validated_result)

    def test_finish_audit_with_none_id(self):
        result = audit_service.finish_ai_audit_log(
            None,
            status="success",
            validation_status="passed",
        )

        self.assertFalse(result)

    @patch.object(audit_service, "SessionLocal")
    def test_finish_audit_record_not_found(self, session_local):
        session_local.return_value = self.db
        self.db.scalar.return_value = None

        result = audit_service.finish_ai_audit_log(
            999,
            status="success",
            validation_status="passed",
        )

        self.assertFalse(result)
        self.db.commit.assert_not_called()
        self.db.close.assert_called_once()

    @patch.object(audit_service, "SessionLocal")
    def test_finish_audit_database_operation_error(self, session_local):
        session_local.return_value = self.db
        self.db.scalar.side_effect = RuntimeError(
            "Database query failed"
        )

        result = audit_service.finish_ai_audit_log(
            123,
            status="success",
            validation_status="passed",
        )

        self.assertFalse(result)
        self.db.rollback.assert_called_once()
        self.db.close.assert_called_once()

    @patch.object(audit_service, "SessionLocal")
    def test_finish_audit_session_creation_error(self, session_local):
        session_local.side_effect = RuntimeError(
            "Database unavailable"
        )

        result = audit_service.finish_ai_audit_log(
            123,
            status="success",
            validation_status="passed",
        )

        self.assertFalse(result)

    # --------------------------------------------------
    # VALIDATION AND ERROR MESSAGE
    # --------------------------------------------------

    @patch.object(audit_service, "SessionLocal")
    def test_finish_audit_rejects_invalid_status(self, session_local):
        with self.assertRaises(ValueError):
            audit_service.finish_ai_audit_log(
                123,
                status="unknown",
                validation_status="passed",
            )

        session_local.assert_not_called()

    @patch.object(audit_service, "SessionLocal")
    def test_finish_audit_rejects_invalid_validation_status(
        self,
        session_local,
    ):
        with self.assertRaises(ValueError):
            audit_service.finish_ai_audit_log(
                123,
                status="success",
                validation_status="unknown",
            )

        session_local.assert_not_called()

    def test_safe_error_message_is_limited(self):
        error = RuntimeError("A" * 5000)

        result = audit_service._safe_error_message(error)

        self.assertEqual(
            len(result),
            audit_service.MAX_ERROR_MESSAGE_LENGTH,
        )

    def test_safe_error_message_uses_exception_type_when_empty(self):
        error = RuntimeError("   ")

        result = audit_service._safe_error_message(error)

        self.assertEqual(result, "RuntimeError")


if __name__ == "__main__":
    unittest.main()
