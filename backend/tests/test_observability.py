import unittest
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models.monitoring import AlertUser
from app.services.ai_audit_context import AIAuditContext
from app.services.monitoring_auth import get_current_user
from app.services.observability import RequestMetrics


class ObservabilityMetricsTests(unittest.TestCase):
    def test_request_metrics_track_latency_throughput_and_status(self):
        metrics = RequestMetrics()
        metrics.record(
            method="GET",
            route="/health/live",
            status_code=200,
            duration_seconds=0.03,
        )
        metrics.record(
            method="GET",
            route="/resource/{resource_id}",
            status_code=503,
            duration_seconds=0.2,
        )

        snapshot = metrics.snapshot()

        self.assertEqual(snapshot["requests_total"], 2)
        self.assertEqual(snapshot["errors_5xx_total"], 1)
        self.assertAlmostEqual(
            snapshot["throughput_requests_per_second_1m"],
            2 / 60,
            places=3,
        )
        self.assertEqual(snapshot["routes"][0]["latency_buckets"]["0.05"], 1)
        self.assertEqual(snapshot["routes"][1]["errors"], 1)
        self.assertEqual(snapshot["routes"][1]["latency_over_10_seconds"], 0)


class AIUsageAccountingTests(unittest.TestCase):
    def setUp(self):
        self.input_price = settings.GEMINI_INPUT_COST_PER_MILLION_TOKENS_USD
        self.output_price = settings.GEMINI_OUTPUT_COST_PER_MILLION_TOKENS_USD
        settings.GEMINI_INPUT_COST_PER_MILLION_TOKENS_USD = 0.5
        settings.GEMINI_OUTPUT_COST_PER_MILLION_TOKENS_USD = 1.5

    def tearDown(self):
        settings.GEMINI_INPUT_COST_PER_MILLION_TOKENS_USD = self.input_price
        settings.GEMINI_OUTPUT_COST_PER_MILLION_TOKENS_USD = self.output_price

    def test_ai_audit_records_token_usage_and_configured_estimate(self):
        with (
            patch(
                "app.services.ai_audit_context.start_ai_audit_log",
                return_value=123,
            ),
            patch(
                "app.services.ai_audit_context.finish_ai_audit_log",
                return_value=True,
            ) as finish,
        ):
            context = AIAuditContext(
                analysis_type="test",
                model_name="test-model",
                prompt_version="test-v1",
            )
            context.set_usage_from_response(
                SimpleNamespace(
                    usage_metadata=SimpleNamespace(
                        prompt_token_count=1000,
                        candidates_token_count=2000,
                    )
                )
            )
            context.succeed({"ok": True})

        self.assertEqual(context.input_tokens, 1000)
        self.assertEqual(context.output_tokens, 2000)
        self.assertEqual(context.estimated_cost_usd, 0.0035)
        self.assertEqual(finish.call_args.kwargs["input_tokens"], 1000)
        self.assertEqual(finish.call_args.kwargs["output_tokens"], 2000)
        self.assertEqual(
            finish.call_args.kwargs["estimated_cost_usd"],
            0.0035,
        )

    def test_cost_is_not_fabricated_when_provider_usage_is_missing(self):
        with patch(
            "app.services.ai_audit_context.start_ai_audit_log",
            return_value=None,
        ):
            context = AIAuditContext(
                analysis_type="test",
                model_name="test-model",
                prompt_version="test-v1",
            )
            context.set_usage_from_response(SimpleNamespace())

        self.assertIsNone(context.input_tokens)
        self.assertIsNone(context.output_tokens)
        self.assertIsNone(context.estimated_cost_usd)


class OperationalEndpointsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.old_db_override = app.dependency_overrides.get(get_db)
        self.old_user_override = app.dependency_overrides.get(get_current_user)

        def override_get_db() -> Iterator[Session]:
            with Session(self.engine) as db:
                yield db

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = lambda: AlertUser(
            id=1,
            email="observability-admin@example.test",
            display_name="Observability Admin",
            password_hash="test-only",
            role="admin",
            is_active=True,
            account_status="active",
        )
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        if self.old_db_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = self.old_db_override
        if self.old_user_override is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = self.old_user_override
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_liveness_readiness_admin_health_and_metrics(self):
        live = self.client.get("/health/live")
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.json(), {"status": "ok"})
        self.assertTrue(live.headers.get("X-Request-ID"))

        ready = self.client.get("/health/ready")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["dependencies"]["database"], "available")

        health = self.client.get("/api/ops/health")
        self.assertEqual(health.status_code, 200, health.text)
        self.assertIn("hardware_analysis_jobs", health.json())
        self.assertIn("integrations", health.json())
        self.assertIn("ai", health.json())

        metrics = self.client.get("/api/ops/metrics")
        self.assertEqual(metrics.status_code, 200, metrics.text)
        metrics = self.client.get("/api/ops/metrics")
        self.assertGreaterEqual(
            metrics.json()["http"]["requests_total"],
            4,
        )

    def test_operational_endpoints_are_admin_only(self):
        app.dependency_overrides[get_current_user] = lambda: AlertUser(
            id=2,
            email="observability-viewer@example.test",
            display_name="Observability Viewer",
            password_hash="test-only",
            role="viewer",
            is_active=True,
            account_status="active",
        )

        response = self.client.get("/api/ops/metrics")

        self.assertEqual(response.status_code, 403)

    def test_readiness_returns_service_unavailable_when_database_fails(self):
        failing_session = Mock()
        failing_session.execute.side_effect = RuntimeError("db unavailable")

        def override_failing_db():
            yield failing_session

        app.dependency_overrides[get_db] = override_failing_db
        response = self.client.get("/health/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["dependencies"]["database"],
            "unavailable",
        )
