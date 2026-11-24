import os
import unittest
import uuid

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database.connection import engine
from app.models import AnalysisHistory
from app.models.monitoring import UserActionAudit
from app.services.analysis_history_service import create_analysis_history
from app.services.system_integration_service import analyze_system_integration


POSTGRES_INTEGRATION_ENABLED = (
    os.environ.get("RUN_POSTGRES_INTEGRATION") == "1"
)


@unittest.skipUnless(
    POSTGRES_INTEGRATION_ENABLED,
    "Set RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL integration tests.",
)
class PostgresSystemIntegrationTests(unittest.TestCase):
    def test_unified_result_and_audit_persist_as_postgres_json(self):
        if engine.dialect.name != "postgresql":
            self.skipTest("Configured database is not PostgreSQL.")

        run_id = uuid.uuid4().hex
        dataframe = pd.DataFrame(
            [
                {
                    "Date": f"2026-10-{day:02d}",
                    "Production": 100 + day,
                    "Inspected": 100,
                    "Defective Units": day,
                    "Machine": "Press 1",
                    "Machine Status": "Fault" if day % 3 == 0 else "Running",
                    "Downtime": day,
                }
                for day in range(1, 21)
            ]
        )
        history_id = None
        try:
            result = analyze_system_integration(
                dataframe,
                problem_statement=f"PostgreSQL integration {run_id}",
                include_ai=False,
                horizon=2,
            )
            with Session(engine) as session:
                history = create_analysis_history(
                    session,
                    analysis_type="system_integration",
                    title="PostgreSQL system integration test",
                    summary=run_id,
                    result_data=result,
                    evidence=result["evidence_references"],
                    algorithm_version="system-integration-v1",
                )
                history_id = history.id
                session.add(
                    UserActionAudit(
                        actor_user_id=None,
                        action="test.system_integration.persisted",
                        object_type="analysis_history",
                        object_id=str(history.id),
                        outcome="success",
                        after_data={
                            "run_id": run_id,
                            "finding_count": len(
                                result["cross_intelligence"]["findings"]
                            ),
                        },
                    )
                )
                session.commit()

            with Session(engine) as session:
                saved = session.get(AnalysisHistory, history_id)
                audit = session.scalar(
                    select(UserActionAudit).where(
                        UserActionAudit.object_id == str(history_id),
                        UserActionAudit.action
                        == "test.system_integration.persisted",
                    )
                )
                self.assertIsNotNone(saved)
                self.assertEqual(
                    saved.result_data["analysis_type"],
                    "system_integration",
                )
                self.assertEqual(
                    set(saved.result_data["domains"]),
                    {
                        "production",
                        "quality",
                        "root_cause",
                        "machine",
                        "material",
                        "predictive",
                    },
                )
                self.assertIsNotNone(audit)
                self.assertEqual(audit.after_data["run_id"], run_id)
        finally:
            if history_id is not None:
                with Session(engine) as session:
                    session.execute(
                        delete(UserActionAudit).where(
                            UserActionAudit.object_id == str(history_id),
                            UserActionAudit.action
                            == "test.system_integration.persisted",
                        )
                    )
                    session.execute(
                        delete(AnalysisHistory).where(
                            AnalysisHistory.id == history_id
                        )
                    )
                    session.commit()


if __name__ == "__main__":
    unittest.main()
