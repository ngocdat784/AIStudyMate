import os
import unittest
import uuid

from sqlalchemy import delete, select

from app.database.connection import engine
from app.models.ai_audit_log import AIAuditLog
from app.services.ai_audit_service import (
    SessionLocal,
    finish_ai_audit_log,
    start_ai_audit_log,
)


POSTGRES_INTEGRATION_ENABLED = (
    os.environ.get("RUN_POSTGRES_INTEGRATION") == "1"
)


@unittest.skipUnless(
    POSTGRES_INTEGRATION_ENABLED,
    "Set RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL integration tests.",
)
class PostgresAIAuditIntegrationTests(unittest.TestCase):
    def test_audit_lifecycle_persists_all_terminal_statuses(self):
        if engine.dialect.name != "postgresql":
            self.skipTest("Configured database is not PostgreSQL.")

        run_id = uuid.uuid4().hex
        audit_ids: list[int] = []
        test_cases = (
            (
                "success",
                "passed",
                None,
                '{"result":"ok"}',
                {"result": "ok"},
            ),
            (
                "error",
                "not_run",
                RuntimeError("Simulated Gemini API error"),
                None,
                None,
            ),
            (
                "validation_failed",
                "failed",
                ValueError("Simulated invalid response"),
                "not valid json",
                None,
            ),
        )

        try:
            for status, validation_status, error, raw, result in test_cases:
                audit_id = start_ai_audit_log(
                    analysis_type="reliability_test",
                    model_name="test-model",
                    prompt_version="reliability_test_v1",
                    input_data={"run_id": run_id},
                    prompt_text=f"Reliability test {run_id}",
                )
                self.assertIsNotNone(audit_id)
                assert audit_id is not None
                audit_ids.append(audit_id)

                self.assertTrue(
                    finish_ai_audit_log(
                        audit_id,
                        status=status,
                        validation_status=validation_status,
                        raw_response=raw,
                        validated_result=result,
                        error=error,
                        duration_ms=17,
                    )
                )

            with SessionLocal() as session:
                records = list(
                    session.scalars(
                        select(AIAuditLog).where(
                            AIAuditLog.id.in_(audit_ids)
                        )
                    )
                )

            records_by_status = {
                record.status: record for record in records
            }
            self.assertEqual(
                set(records_by_status),
                {"success", "error", "validation_failed"},
            )

            success = records_by_status["success"]
            self.assertEqual(success.validation_status, "passed")
            self.assertEqual(success.raw_response, '{"result":"ok"}')
            self.assertEqual(success.validated_result, {"result": "ok"})
            self.assertIsNone(success.error_type)
            self.assertIsNotNone(success.completed_at)

            api_error = records_by_status["error"]
            self.assertEqual(api_error.validation_status, "not_run")
            self.assertEqual(api_error.error_type, "RuntimeError")
            self.assertEqual(
                api_error.error_message,
                "Simulated Gemini API error",
            )
            self.assertIsNone(api_error.raw_response)

            validation_error = records_by_status["validation_failed"]
            self.assertEqual(validation_error.validation_status, "failed")
            self.assertEqual(
                validation_error.error_type,
                "ValueError",
            )
            self.assertEqual(
                validation_error.raw_response,
                "not valid json",
            )
            self.assertEqual(validation_error.duration_ms, 17)
            self.assertEqual(validation_error.input_data["run_id"], run_id)
        finally:
            if audit_ids:
                with SessionLocal() as session:
                    session.execute(
                        delete(AIAuditLog).where(
                            AIAuditLog.id.in_(audit_ids)
                        )
                    )
                    session.commit()


if __name__ == "__main__":
    unittest.main()
