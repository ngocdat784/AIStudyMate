from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import unittest
from typing import Iterator
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database.base import Base
from app.database.connection import engine, get_db
from app.main import app
from app.models.monitoring import (
    Alert,
    AlertDelivery,
    AlertNotification,
    AlertRule,
    AlertStatusHistory,
    AlertUser,
    AlertUserFactory,
    AlertUserPlant,
    Department,
    MonitoringDataset,
    Organization,
    Factory,
    Plant,
    ProductionLine,
    UserActionAudit,
)
from app.services.monitoring_service import (
    ALERT_DELIVERY_LOCK_KEY,
    DATASET_EVALUATION_LOCK_KEY,
    SNOOZE_EXPIRY_LOCK_KEY,
    _anomaly_candidate,
    _statistical_candidates,
    _send_webhook,
    deliver_pending_alert_notifications,
    evaluate_due_datasets,
    evaluate_monitoring_dataset,
    reopen_expired_snoozes,
)
from app.services.monitoring_lock import postgres_advisory_lock
from app.services.monitoring_scheduler import (
    SCHEDULER_LOCK_KEY,
    scheduler as monitoring_scheduler,
    start_scheduler,
)


class MonitoringDetectorTests(unittest.TestCase):
    def test_rolling_detector_needs_eight_past_periods(self):
        seven_past = [
            {"period_start": f"2026-01-{day:02d}", "defect_rate": 2.0}
            for day in range(1, 8)
        ]
        seven_past.append(
            {"period_start": "2026-01-08", "defect_rate": 30.0}
        )
        self.assertIsNone(
            _anomaly_candidate(seven_past, "defect_rate", "plant", None)
        )

    def test_rolling_detector_uses_past_only_and_combines_detectors(self):
        values = [2.0, 3.0, 2.5, 3.5, 2.0, 3.0, 2.5, 4.0, 30.0]
        series = [
            {
                "period_start": f"2026-01-{day:02d}",
                "defect_rate": value,
                "production": 1000,
                "defects": value * 10,
            }
            for day, value in enumerate(values, start=1)
        ]
        candidate = _anomaly_candidate(
            series, "defect_rate", "plant", None
        )

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(len(candidate["baseline"]), 8)
        self.assertEqual(candidate["observed"], 30.0)
        self.assertTrue(candidate["iqr_triggered"])
        self.assertTrue(candidate["z_score_triggered"])
        self.assertEqual(candidate["period"], "2026-01-09")

    def test_product_baselines_do_not_mix_other_products(self):
        records = []
        for day in range(1, 10):
            records.extend(
                [
                    {
                        "Date": f"2026-01-{day:02d}",
                        "Product": "Product A",
                        "Production": 100,
                        "Defects": 30 if day == 9 else 2,
                        "Downtime": 1,
                    },
                    {
                        "Date": f"2026-01-{day:02d}",
                        "Product": "Product B",
                        "Production": 100,
                        "Defects": 20,
                        "Downtime": 1,
                    },
                ]
            )

        candidates = _statistical_candidates(records, "day")
        product_a = [
            item
            for item in candidates
            if item["scope_type"] == "product"
            and item["scope_value"] == "Product A"
            and item["metric"] == "defect_rate"
        ]
        product_b = [
            item
            for item in candidates
            if item["scope_type"] == "product"
            and item["scope_value"] == "Product B"
            and item["metric"] == "defect_rate"
        ]
        self.assertEqual(len(product_a), 1)
        self.assertEqual(len(product_a[0]["baseline"]), 8)
        self.assertEqual(product_a[0]["observed"], 30.0)
        self.assertEqual(product_b, [])


class MonitoringPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.gemini_api_key = settings.GEMINI_API_KEY
        settings.GEMINI_API_KEY = None
        self.delivery_settings = (
            settings.ALERT_SMTP_HOST,
            settings.ALERT_SMTP_PORT,
            settings.ALERT_SMTP_USERNAME,
            settings.ALERT_SMTP_PASSWORD,
            settings.ALERT_SMTP_FROM,
            settings.ALERT_WEBHOOK_URL,
        )
        settings.ALERT_SMTP_HOST = None
        settings.ALERT_SMTP_FROM = None
        settings.ALERT_WEBHOOK_URL = None
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            self.user = AlertUser(
                email="operator@example.test",
                display_name="Operator",
                password_hash="unused",
                role="operator",
            )
            self.plant = Plant(name="Plant A", timezone="UTC")
            self.organization = Organization(name="Test Organization")
            session.add_all([self.user, self.organization])
            session.flush()
            self.factory = Factory(
                name="Test Factory",
                organization_id=self.organization.id,
            )
            session.add(self.factory)
            session.flush()
            self.plant.factory_id = self.factory.id
            session.add(self.plant)
            session.flush()
            session.add(
                AlertUserFactory(
                    user_id=self.user.id,
                    factory_id=self.factory.id,
                    assigned_by=self.user.id,
                )
            )
            session.add(
                AlertUserPlant(
                    user_id=self.user.id,
                    plant_id=self.plant.id,
                    assigned_by=self.user.id,
                )
            )
            self.dataset = MonitoringDataset(
                name="snapshot",
                plant_id=self.plant.id,
                line_id=None,
                uploaded_by=self.user.id,
                granularity="day",
                schedule_interval=1,
                schedule_unit="days",
                enabled=True,
                data_rows=[
                    {
                        "Date": "2026-10-01",
                        "Product": "Board",
                        "Supplier": "Supplier A",
                        "Batch": "B-1",
                        "Production": 100,
                        "Defects": 10,
                        "Downtime": 4,
                    },
                    {
                        "Date": "2026-10-02",
                        "Product": "Board",
                        "Supplier": "Supplier A",
                        "Batch": "B-2",
                        "Production": 100,
                        "Defects": 10,
                        "Downtime": 3,
                    },
                ],
            )
            session.add(self.dataset)
            session.add(
                AlertRule(
                    name="Defect rate over 5%",
                    plant_id=self.plant.id,
                    line_id=None,
                    product=None,
                    metric="defect_rate",
                    operator="gt",
                    threshold=5,
                    severity="high",
                    enabled=True,
                    created_by=self.user.id,
                )
            )
            session.commit()
            self.dataset_id = self.dataset.id
            self.plant_id = self.plant.id

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        settings.GEMINI_API_KEY = self.gemini_api_key
        (
            settings.ALERT_SMTP_HOST,
            settings.ALERT_SMTP_PORT,
            settings.ALERT_SMTP_USERNAME,
            settings.ALERT_SMTP_PASSWORD,
            settings.ALERT_SMTP_FROM,
            settings.ALERT_WEBHOOK_URL,
        ) = self.delivery_settings

    def test_database_rejects_plant_department_from_another_factory(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(dbapi_connection, _):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        try:
            Base.metadata.create_all(engine)
            with Session(engine) as session:
                organization = Organization(name="Organization Test")
                session.add(organization)
                session.flush()
                factory_a = Factory(
                    name="Factory A",
                    organization_id=organization.id,
                )
                factory_b = Factory(
                    name="Factory B",
                    organization_id=organization.id,
                )
                session.add_all([factory_a, factory_b])
                session.flush()
                department_a = Department(
                    name="Department A",
                    factory_id=factory_a.id,
                )
                session.add(department_a)
                session.flush()
                session.add(
                    Plant(
                        name="Plant B",
                        factory_id=factory_b.id,
                        department_id=department_a.id,
                    )
                )

                with self.assertRaises(IntegrityError):
                    session.flush()
        finally:
            engine.dispose()

    def _insert_alert(
        self,
        *,
        status: str,
        snoozed_until: datetime | None = None,
        resolved_at: datetime | None = None,
    ) -> int:
        with Session(self.engine) as session:
            alert = Alert(
                dedupe_key=f"test-alert-{status}-{datetime.now(timezone.utc).timestamp()}",
                alert_type="threshold",
                rule_id=None,
                plant_id=self.plant_id,
                line_id=None,
                metric="defect_rate",
                scope_type="plant",
                scope_value=None,
                severity="high",
                title="Test alert",
                summary="Test alert summary",
                observed_value=10,
                threshold_value=5,
                impact={"production": 100, "defects": 10},
                evidence=[],
                recommended_check="Check data",
                confidence="high",
                status=status,
                snoozed_until=snoozed_until,
                detected_at=datetime.now(timezone.utc),
                last_seen_at=datetime.now(timezone.utc),
                resolved_at=resolved_at,
            )
            session.add(alert)
            session.commit()
            return alert.id

    def test_threshold_evaluation_is_python_only_and_deduplicated(self):
        with Session(self.engine) as session:
            dataset = session.get(MonitoringDataset, self.dataset_id)
            assert dataset is not None
            first = evaluate_monitoring_dataset(session, dataset)
            second = evaluate_monitoring_dataset(session, dataset)

            alerts = session.scalars(select(Alert)).all()
            notification_count = session.scalar(
                select(func.count(AlertNotification.id))
            )
            self.assertEqual(first["alerts_detected"], 1)
            self.assertEqual(second["alerts_detected"], 1)
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0].alert_type, "threshold")
            self.assertEqual(alerts[0].observed_value, 10.0)
            self.assertEqual(alerts[0].status, "open")
            self.assertEqual(notification_count, 1)
            self.assertIn("ngưỡng", alerts[0].summary or "")
            self.assertEqual(alerts[0].ai_explanation_status, "not_configured")

    def test_delivery_attempts_email_and_https_webhook_once(self):
        settings.ALERT_SMTP_HOST = "smtp.example.test"
        settings.ALERT_SMTP_FROM = "alerts@example.test"
        settings.ALERT_WEBHOOK_URL = "https://hooks.example.test/alerts"
        with Session(self.engine) as session:
            user = session.get(AlertUser, 1)
            assert user is not None
            user.email_notifications = True
            dataset = session.get(MonitoringDataset, self.dataset_id)
            assert dataset is not None
            evaluate_monitoring_dataset(session, dataset)

        with (
            patch(
                "app.services.monitoring_service.SessionLocal",
                side_effect=lambda: Session(self.engine),
            ),
            patch("app.services.monitoring_service._send_email") as send_email,
            patch("app.services.monitoring_service._send_webhook") as send_webhook,
        ):
            deliver_pending_alert_notifications()
            deliver_pending_alert_notifications()

        with Session(self.engine) as session:
            deliveries = session.scalars(select(AlertDelivery)).all()
            self.assertEqual(len(deliveries), 2)
            self.assertTrue(all(item.status == "sent" for item in deliveries))
            self.assertEqual(send_email.call_count, 1)
            self.assertEqual(send_webhook.call_count, 1)

    def test_delivery_job_skips_when_another_worker_holds_lock(self):
        if engine.dialect.name != "postgresql":
            self.skipTest("PostgreSQL advisory locks are required.")

        with postgres_advisory_lock(ALERT_DELIVERY_LOCK_KEY) as acquired:
            self.assertTrue(acquired)
            with (
                patch("app.services.monitoring_service.SessionLocal") as session_local,
                patch("app.services.monitoring_service._send_email") as send_email,
                patch("app.services.monitoring_service._send_webhook") as send_webhook,
            ):
                deliver_pending_alert_notifications()
            session_local.assert_not_called()
            send_email.assert_not_called()
            send_webhook.assert_not_called()

    def test_dataset_job_skips_when_another_worker_holds_lock(self):
        if engine.dialect.name != "postgresql":
            self.skipTest("PostgreSQL advisory locks are required.")

        with postgres_advisory_lock(DATASET_EVALUATION_LOCK_KEY) as acquired:
            self.assertTrue(acquired)
            with patch(
                "app.services.monitoring_service.SessionLocal"
            ) as session_local:
                evaluate_due_datasets()
            session_local.assert_not_called()

    def test_scheduler_does_not_start_when_another_worker_owns_it(self):
        if engine.dialect.name != "postgresql":
            self.skipTest("PostgreSQL advisory locks are required.")
        if monitoring_scheduler.running:
            self.skipTest("The shared test scheduler is already running.")

        with postgres_advisory_lock(SCHEDULER_LOCK_KEY) as acquired:
            self.assertTrue(acquired)
            with patch.object(monitoring_scheduler, "start") as scheduler_start:
                self.assertFalse(start_scheduler())
            scheduler_start.assert_not_called()

    def test_expired_snooze_reopens_alert_and_records_history(self):
        alert_id = self._insert_alert(
            status="snoozed",
            snoozed_until=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        with (
            patch(
                "app.services.monitoring_service.postgres_advisory_lock",
                return_value=nullcontext(True),
            ),
            patch(
                "app.services.monitoring_service.SessionLocal",
                side_effect=lambda: Session(self.engine),
            ),
        ):
            reopen_expired_snoozes()

        with Session(self.engine) as session:
            alert = session.get(Alert, alert_id)
            self.assertIsNotNone(alert)
            self.assertEqual(alert.status, "open")
            self.assertIsNone(alert.snoozed_until)
            events = session.scalars(
                select(AlertStatusHistory).where(
                    AlertStatusHistory.alert_id == alert_id,
                    AlertStatusHistory.event_type == "snooze_expired",
                )
            ).all()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].from_status, "snoozed")
            self.assertEqual(events[0].to_status, "open")

    def test_unexpired_snooze_remains_unchanged(self):
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        alert_id = self._insert_alert(
            status="snoozed",
            snoozed_until=expires_at,
        )
        with (
            patch(
                "app.services.monitoring_service.postgres_advisory_lock",
                return_value=nullcontext(True),
            ),
            patch(
                "app.services.monitoring_service.SessionLocal",
                side_effect=lambda: Session(self.engine),
            ),
        ):
            reopen_expired_snoozes()

        with Session(self.engine) as session:
            alert = session.get(Alert, alert_id)
            self.assertIsNotNone(alert)
            self.assertEqual(alert.status, "snoozed")
            self.assertEqual(
                alert.snoozed_until.replace(tzinfo=timezone.utc),
                expires_at,
            )
            event_count = session.scalar(
                select(func.count(AlertStatusHistory.id)).where(
                    AlertStatusHistory.alert_id == alert_id
                )
            )
            self.assertEqual(event_count, 0)

    def test_non_snoozed_alert_is_not_modified_by_expiry_job(self):
        resolved_at = datetime.now(timezone.utc) - timedelta(hours=2)
        alerts = {
            "open": self._insert_alert(status="open"),
            "resolved": self._insert_alert(
                status="resolved",
                resolved_at=resolved_at,
            ),
            "closed": self._insert_alert(
                status="closed",
                resolved_at=resolved_at,
            ),
        }
        with (
            patch(
                "app.services.monitoring_service.postgres_advisory_lock",
                return_value=nullcontext(True),
            ),
            patch(
                "app.services.monitoring_service.SessionLocal",
                side_effect=lambda: Session(self.engine),
            ),
        ):
            reopen_expired_snoozes()

        with Session(self.engine) as session:
            for expected_status, alert_id in alerts.items():
                alert = session.get(Alert, alert_id)
                self.assertIsNotNone(alert)
                self.assertEqual(alert.status, expected_status)
                self.assertIsNone(alert.snoozed_until)
                self.assertEqual(
                    (
                        alert.resolved_at.replace(tzinfo=timezone.utc)
                        if alert.resolved_at is not None
                        else None
                    ),
                    resolved_at
                    if expected_status in ("resolved", "closed")
                    else None,
                )
            event_count = session.scalar(
                select(func.count(AlertStatusHistory.id)).where(
                    AlertStatusHistory.event_type == "snooze_expired"
                )
            )
            self.assertEqual(event_count, 0)

    def test_snooze_expiry_job_skips_when_another_worker_holds_lock(self):
        alert_id = self._insert_alert(
            status="snoozed",
            snoozed_until=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        with (
            patch(
                "app.services.monitoring_service.postgres_advisory_lock",
                return_value=nullcontext(False),
            ),
            patch("app.services.monitoring_service.SessionLocal") as session_local,
        ):
            reopen_expired_snoozes()

        session_local.assert_not_called()
        with Session(self.engine) as session:
            alert = session.get(Alert, alert_id)
            self.assertIsNotNone(alert)
            self.assertEqual(alert.status, "snoozed")
            self.assertIsNotNone(alert.snoozed_until)
            event_count = session.scalar(
                select(func.count(AlertStatusHistory.id)).where(
                    AlertStatusHistory.alert_id == alert_id,
                    AlertStatusHistory.event_type == "snooze_expired",
                )
            )
            self.assertEqual(event_count, 0)

    def test_due_evaluation_uses_newest_enabled_dataset_for_each_scope(self):
        with Session(self.engine) as session:
            older = session.get(MonitoringDataset, self.dataset_id)
            assert older is not None
            newer_disabled = MonitoringDataset(
                name="newer disabled snapshot",
                plant_id=older.plant_id,
                line_id=older.line_id,
                uploaded_by=older.uploaded_by,
                granularity=older.granularity,
                schedule_interval=older.schedule_interval,
                schedule_unit=older.schedule_unit,
                enabled=False,
                data_rows=older.data_rows,
            )
            session.add(newer_disabled)
            session.commit()
            older_id = older.id

        with (
            patch(
                "app.services.monitoring_service.postgres_advisory_lock",
                return_value=nullcontext(True),
            ),
            patch(
                "app.services.monitoring_service.SessionLocal",
                side_effect=lambda: Session(self.engine),
            ),
            patch(
                "app.services.monitoring_service.evaluate_monitoring_dataset"
            ) as evaluate,
        ):
            evaluate_due_datasets()

        evaluate.assert_called_once()
        self.assertEqual(evaluate.call_args.args[1].id, older_id)

    def test_anomaly_alert_uses_dataset_plant_line_and_product_scope(self):
        records = []
        for day in range(1, 10):
            records.extend(
                [
                    {
                        "Date": f"2026-01-{day:02d}",
                        "Product": "Product A",
                        "Production": 100,
                        "Defects": 30 if day == 9 else 2,
                        "Downtime": 1,
                    },
                    {
                        "Date": f"2026-01-{day:02d}",
                        "Product": "Product B",
                        "Production": 100,
                        "Defects": 20,
                        "Downtime": 1,
                    },
                ]
            )
        with Session(self.engine) as session:
            dataset = session.get(MonitoringDataset, self.dataset_id)
            assert dataset is not None
            line = ProductionLine(plant_id=dataset.plant_id, name="Line 1")
            session.add(line)
            session.flush()
            dataset.line_id = line.id
            dataset.data_rows = records
            evaluate_monitoring_dataset(session, dataset)

            product_alerts = session.scalars(
                select(Alert).where(
                    Alert.alert_type == "statistical",
                    Alert.scope_type == "product",
                    Alert.scope_value == "Product A",
                    Alert.metric == "defect_rate",
                )
            ).all()
            self.assertEqual(len(product_alerts), 1)
            self.assertEqual(product_alerts[0].plant_id, dataset.plant_id)
            self.assertEqual(product_alerts[0].line_id, line.id)
            self.assertEqual(product_alerts[0].evidence[1]["baseline_observations"], 8)

    def test_severity_escalation_is_persisted_and_notified(self):
        settings.ALERT_WEBHOOK_URL = "https://hooks.example.test/alerts"
        with Session(self.engine) as session:
            dataset = session.get(MonitoringDataset, self.dataset_id)
            rule = session.scalar(select(AlertRule))
            assert dataset is not None and rule is not None
            evaluate_monitoring_dataset(session, dataset)
            rule.severity = "critical"
            session.flush()
            evaluate_monitoring_dataset(session, dataset)

            events = session.scalars(
                select(AlertStatusHistory).where(
                    AlertStatusHistory.event_type == "severity_escalated"
                )
            ).all()
            self.assertEqual(len(events), 1)
            notifications = session.scalars(
                select(AlertNotification).where(
                    AlertNotification.event_key == f"event:{events[0].id}"
                )
            ).all()
            deliveries = session.scalars(
                select(AlertDelivery)
                .where(AlertDelivery.channel == "webhook")
                .order_by(AlertDelivery.id)
            ).all()

            self.assertEqual(
                events[0].event_data,
                {
                    "previous_severity": "high",
                    "severity": "critical",
                    "period": "2026-10-02",
                },
            )
            self.assertEqual(len(notifications), 1)
            self.assertIn("high → critical", notifications[0].message)
            self.assertEqual(len(deliveries), 2)
            self.assertEqual(deliveries[-1].payload["severity"], "critical")
            self.assertEqual(
                deliveries[-1].payload["event_type"],
                "severity_escalated",
            )

    def test_webhook_rejects_private_dns_addresses(self):
        settings.ALERT_WEBHOOK_URL = "https://internal.example.test/hook"
        with Session(self.engine) as session:
            dataset = session.get(MonitoringDataset, self.dataset_id)
            assert dataset is not None
            evaluate_monitoring_dataset(session, dataset)
            alert = session.scalar(select(Alert))
            assert alert is not None

        with (
            patch(
                "app.services.monitoring_service.socket.getaddrinfo",
                return_value=[
                    (2, 1, 6, "", ("127.0.0.1", 443))
                ],
            ),
            patch("app.services.monitoring_service.socket.socket") as make_socket,
        ):
            with self.assertRaisesRegex(RuntimeError, "public IP"):
                _send_webhook(alert, "test-delivery")
        make_socket.assert_not_called()

    def test_webhook_does_not_follow_redirects(self):
        settings.ALERT_WEBHOOK_URL = "https://hooks.example.test/alerts"
        with Session(self.engine) as session:
            dataset = session.get(MonitoringDataset, self.dataset_id)
            assert dataset is not None
            evaluate_monitoring_dataset(session, dataset)
            alert = session.scalar(select(Alert))
            assert alert is not None

        class FakeSocket:
            def settimeout(self, _timeout):
                pass

            def connect(self, _address):
                pass

            def getpeername(self):
                return ("93.184.216.34", 443)

            def close(self):
                pass

        class FakeTLSContext:
            def wrap_socket(self, raw_socket, server_hostname):
                self.server_hostname = server_hostname
                return raw_socket

        response = Mock(status=302)
        connection = Mock()
        connection.getresponse.return_value = response
        tls_context = FakeTLSContext()
        with (
            patch(
                "app.services.monitoring_service.socket.getaddrinfo",
                return_value=[
                    (2, 1, 6, "", ("93.184.216.34", 443))
                ],
            ),
            patch(
                "app.services.monitoring_service.socket.socket",
                return_value=FakeSocket(),
            ),
            patch(
                "app.services.monitoring_service.ssl.create_default_context",
                return_value=tls_context,
            ),
            patch(
                "app.services.monitoring_service.http.client.HTTPSConnection",
                return_value=connection,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "HTTP 302"):
                _send_webhook(alert, "test-delivery")
        connection.request.assert_called_once()
        connection.close.assert_called_once()

    def test_ai_explanation_failure_does_not_change_python_alert(self):
        settings.GEMINI_API_KEY = "test-api-key"
        with (
            Session(self.engine) as session,
            patch(
                "app.services.monitoring_service.generate_custom_hardware_response",
                side_effect=RuntimeError("AI unavailable"),
            ),
        ):
            dataset = session.get(MonitoringDataset, self.dataset_id)
            assert dataset is not None
            result = evaluate_monitoring_dataset(session, dataset)
            alert = session.scalar(select(Alert))

            self.assertEqual(result["alerts_detected"], 1)
            self.assertIsNotNone(alert)
            assert alert is not None
            self.assertEqual(alert.alert_type, "threshold")
            self.assertEqual(alert.ai_explanation_status, "unavailable")

class MonitoringAPITests(unittest.TestCase):
    def setUp(self):
        self.gemini_api_key = settings.GEMINI_API_KEY
        settings.GEMINI_API_KEY = None
        self.delivery_settings = (
            settings.ALERT_SMTP_HOST,
            settings.ALERT_SMTP_PORT,
            settings.ALERT_SMTP_USERNAME,
            settings.ALERT_SMTP_PASSWORD,
            settings.ALERT_SMTP_FROM,
            settings.ALERT_WEBHOOK_URL,
        )
        settings.ALERT_SMTP_HOST = None
        settings.ALERT_SMTP_FROM = None
        settings.ALERT_WEBHOOK_URL = None
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            organization = Organization(name="API Test Organization")
            session.add(organization)
            session.flush()
            factory = Factory(
                name="API Test Factory",
                organization_id=organization.id,
            )
            session.add(factory)
            session.commit()
            self.organization_id = organization.id
            self.factory_id = factory.id

        def override_get_db() -> Iterator[Session]:
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        self.secret = settings.ALERT_AUTH_SECRET
        self.bootstrap = settings.ALERT_BOOTSTRAP_TOKEN
        settings.ALERT_AUTH_SECRET = "test-monitoring-secret-with-32-bytes-minimum"
        settings.ALERT_BOOTSTRAP_TOKEN = "test-bootstrap-token"
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.pop(get_db, None)
        settings.ALERT_AUTH_SECRET = self.secret
        settings.ALERT_BOOTSTRAP_TOKEN = self.bootstrap
        settings.GEMINI_API_KEY = self.gemini_api_key
        (
            settings.ALERT_SMTP_HOST,
            settings.ALERT_SMTP_PORT,
            settings.ALERT_SMTP_USERNAME,
            settings.ALERT_SMTP_PASSWORD,
            settings.ALERT_SMTP_FROM,
            settings.ALERT_WEBHOOK_URL,
        ) = self.delivery_settings
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _create_admin(self) -> str:
        response = self.client.post(
            "/api/monitoring/auth/bootstrap-admin",
            json={
                "email": "admin@example.test",
                "password": "secure-test-password",
                "display_name": "Monitoring Admin",
                "bootstrap_token": "test-bootstrap-token",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["access_token"]

    def test_public_signup_defaults_to_viewer_and_cannot_create_plant(self):
        response = self.client.post(
            "/api/monitoring/auth/signup",
            json={
                "email": "viewer@example.test",
                "password": "secure-test-password",
                "display_name": "Viewer",
            },
        )

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["user"]["role"], "viewer")
        denied = self.client.post(
            "/api/monitoring/plants",
            headers={"Authorization": f"Bearer {response.json()['access_token']}"},
            json={
                "name": "Unauthorized",
                "factory_id": self.factory_id,
            },
        )
        self.assertEqual(denied.status_code, 403)

    def test_upload_creates_snapshot_evaluates_and_emits_in_app_alert(self):
        token = self._create_admin()
        headers = {"Authorization": f"Bearer {token}"}
        plant_response = self.client.post(
            "/api/monitoring/plants",
            headers=headers,
            json={
                "name": "Upload Plant",
                "timezone": "UTC",
                "factory_id": self.factory_id,
            },
        )
        self.assertEqual(plant_response.status_code, 201, plant_response.text)
        plant_id = plant_response.json()["id"]
        rule_response = self.client.post(
            "/api/monitoring/rules",
            headers=headers,
            json={
                "name": "High defects",
                "plant_id": plant_id,
                "metric": "defect_rate",
                "operator": "gt",
                "threshold": 5,
            },
        )
        self.assertEqual(rule_response.status_code, 201, rule_response.text)

        upload_response = self.client.post(
            "/api/monitoring/upload",
            headers=headers,
            data={"plant_id": str(plant_id), "granularity": "day"},
            files=[
                (
                    "files",
                    (
                        "snapshot.csv",
                        b"Date,Product,Supplier,Batch,Production,Defects,Downtime\n"
                        b"2026-10-01,Board,Supplier A,B-1,100,10,4\n",
                        "text/csv",
                    ),
                )
            ],
        )

        self.assertEqual(upload_response.status_code, 201, upload_response.text)
        self.assertEqual(upload_response.json()["rows_merged"], 1)
        self.assertEqual(upload_response.json()["alerts_detected"], 1)
        alerts = self.client.get("/api/monitoring/alerts", headers=headers)
        notifications = self.client.get(
            "/api/monitoring/notifications", headers=headers
        )
        self.assertEqual(alerts.status_code, 200)
        self.assertEqual(len(alerts.json()), 1)
        self.assertEqual(alerts.json()[0]["alert_type"], "threshold")
        self.assertEqual(len(notifications.json()), 1)
        self.assertEqual(notifications.json()[0]["alert_id"], alerts.json()[0]["id"])
        alert_id = alerts.json()[0]["id"]

        updated = self.client.patch(
            f"/api/monitoring/alerts/{alert_id}",
            headers=headers,
            json={"status": "acknowledged", "assigned_to": 1},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["status"], "acknowledged")
        self.assertEqual(updated.json()["assigned_to"], 1)
        feedback = self.client.patch(
            f"/api/monitoring/alerts/{alert_id}",
            headers=headers,
            json={
                "assigned_to": None,
                "feedback": "false_positive",
                "feedback_note": "Dữ liệu đầu vào bị sai.",
            },
        )
        self.assertEqual(feedback.status_code, 200, feedback.text)
        self.assertIsNone(feedback.json()["assigned_to"])
        self.assertEqual(feedback.json()["feedback"], "false_positive")
        dashboard = self.client.get("/api/monitoring/dashboard", headers=headers)
        self.assertEqual(dashboard.status_code, 200, dashboard.text)
        self.assertEqual(dashboard.json()["false_positive_rate"], 1.0)
        dataset_id = upload_response.json()["id"]
        deleted = self.client.delete(
            f"/api/monitoring/datasets/{dataset_id}",
            headers=headers,
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(deleted.json()["monitoring_paused"])
        retained_alerts = self.client.get(
            "/api/monitoring/alerts", headers=headers
        )
        self.assertEqual(len(retained_alerts.json()), 1)

    def test_plant_acl_filters_reads_and_enforces_writes(self):
        admin_token = self._create_admin()
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        plants = []
        for name in ("Plant A", "Plant B"):
            response = self.client.post(
                "/api/monitoring/plants",
                headers=admin_headers,
                json={
                    "name": name,
                    "timezone": "UTC",
                    "factory_id": self.factory_id,
                },
            )
            self.assertEqual(response.status_code, 201, response.text)
            plants.append(response.json()["id"])
        plant_a, plant_b = plants

        operator_response = self.client.post(
            "/api/monitoring/users",
            headers=admin_headers,
            json={
                "email": "scoped-operator@example.test",
                "password": "secure-test-password",
                "display_name": "Scoped Operator",
                "role": "operator",
            },
        )
        viewer_response = self.client.post(
            "/api/monitoring/users",
            headers=admin_headers,
            json={
                "email": "scoped-viewer@example.test",
                "password": "secure-test-password",
                "display_name": "Scoped Viewer",
                "role": "viewer",
            },
        )
        self.assertEqual(operator_response.status_code, 201, operator_response.text)
        self.assertEqual(viewer_response.status_code, 201, viewer_response.text)
        operator_id = operator_response.json()["id"]
        viewer_id = viewer_response.json()["id"]
        deactivated = self.client.patch(
            f"/api/monitoring/users/{operator_id}",
            headers=admin_headers,
            json={"role": "operator", "is_active": False},
        )
        self.assertEqual(deactivated.status_code, 200, deactivated.text)
        role_updated = self.client.patch(
            f"/api/monitoring/users/{operator_id}",
            headers=admin_headers,
            json={"role": "operator"},
        )
        self.assertEqual(role_updated.status_code, 200, role_updated.text)
        self.assertFalse(role_updated.json()["is_active"])
        reactivated = self.client.patch(
            f"/api/monitoring/users/{operator_id}",
            headers=admin_headers,
            json={"role": "operator", "is_active": True},
        )
        self.assertTrue(reactivated.json()["is_active"])
        operator_token = self.client.post(
            "/api/monitoring/auth/login",
            json={
                "email": "scoped-operator@example.test",
                "password": "secure-test-password",
            },
        ).json()["access_token"]
        viewer_token = self.client.post(
            "/api/monitoring/auth/login",
            json={
                "email": "scoped-viewer@example.test",
                "password": "secure-test-password",
            },
        ).json()["access_token"]
        operator_headers = {"Authorization": f"Bearer {operator_token}"}
        viewer_headers = {"Authorization": f"Bearer {viewer_token}"}

        for user_id in (operator_id, viewer_id):
            assigned = self.client.put(
                f"/api/monitoring/users/{user_id}/organization-scope",
                headers=admin_headers,
                json={
                    "factory_ids": [self.factory_id],
                    "department_ids": [],
                    "plant_ids": [plant_a],
                },
            )
            self.assertEqual(assigned.status_code, 200, assigned.text)

        rule_response = self.client.post(
            "/api/monitoring/rules",
            headers=admin_headers,
            json={
                "name": "Plant B threshold",
                "plant_id": plant_b,
                "metric": "defect_rate",
                "operator": "gt",
                "threshold": 5,
            },
        )
        self.assertEqual(rule_response.status_code, 201, rule_response.text)
        dataset_response = self.client.post(
            "/api/monitoring/datasets",
            headers=admin_headers,
            json={
                "name": "Plant B snapshot",
                "plant_id": plant_b,
                "data_rows": [
                    {
                        "Date": "2026-10-01",
                        "Production": 100,
                        "Defects": 10,
                    }
                ],
            },
        )
        self.assertEqual(dataset_response.status_code, 201, dataset_response.text)
        dataset_id = dataset_response.json()["id"]
        alert_id = self.client.get(
            "/api/monitoring/alerts", headers=admin_headers
        ).json()[0]["id"]

        self.assertEqual(
            [plant["id"] for plant in self.client.get(
                "/api/monitoring/plants", headers=operator_headers
            ).json()],
            [plant_a],
        )
        self.assertEqual(
            self.client.get("/api/monitoring/datasets", headers=operator_headers).json(),
            [],
        )
        self.assertEqual(
            self.client.get("/api/monitoring/rules", headers=operator_headers).json(),
            [],
        )
        self.assertEqual(
            self.client.get("/api/monitoring/alerts", headers=operator_headers).json(),
            [],
        )
        self.assertEqual(
            self.client.get(
                f"/api/monitoring/alerts?plant_id={plant_b}",
                headers=operator_headers,
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.patch(
                f"/api/monitoring/rules/{rule_response.json()['id']}",
                headers=operator_headers,
                json={
                    "name": "Moved rule",
                    "plant_id": plant_b,
                    "metric": "defect_rate",
                    "operator": "gt",
                    "threshold": 6,
                },
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.patch(
                f"/api/monitoring/alerts/{alert_id}",
                headers=operator_headers,
                json={"status": "acknowledged"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.patch(
                f"/api/monitoring/datasets/{dataset_id}/schedule",
                headers=operator_headers,
                json={"interval": 1, "unit": "days", "enabled": False},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.delete(
                f"/api/monitoring/datasets/{dataset_id}",
                headers=operator_headers,
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.patch(
                f"/api/monitoring/alerts/{alert_id}",
                headers=viewer_headers,
                json={"status": "acknowledged"},
            ).status_code,
            403,
        )
        with Session(self.engine) as session:
            denied_action = session.scalar(
                select(UserActionAudit).where(
                    UserActionAudit.actor_user_id == viewer_id,
                    UserActionAudit.action == "access.denied",
                    UserActionAudit.object_type == "api_route",
                    UserActionAudit.object_id == f"/api/monitoring/alerts/{alert_id}",
                    UserActionAudit.outcome == "denied",
                )
            )
            self.assertIsNotNone(denied_action)
        self.assertEqual(
            self.client.patch(
                f"/api/monitoring/alerts/{alert_id}",
                headers=admin_headers,
                json={"assigned_to": operator_id},
            ).status_code,
            403,
        )

        revoked = self.client.put(
            f"/api/monitoring/users/{operator_id}/plants",
            headers=admin_headers,
            json={"plant_ids": []},
        )
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(
            self.client.get("/api/monitoring/plants", headers=operator_headers).json(),
            [],
        )
        self.assertEqual(
            self.client.get(
                f"/api/monitoring/alerts/{alert_id}/history",
                headers=operator_headers,
            ).status_code,
            403,
        )

    def test_multi_factory_department_assignments_enforce_hierarchy(self):
        admin_token = self._create_admin()
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        second_factory = self.client.post(
            "/api/monitoring/factories",
            headers=admin_headers,
            json={
                "organization_id": self.organization_id,
                "name": "Second acceptance factory",
            },
        )
        self.assertEqual(second_factory.status_code, 201, second_factory.text)
        factory_b_id = second_factory.json()["id"]

        department_a = self.client.post(
            "/api/monitoring/departments",
            headers=admin_headers,
            json={"factory_id": self.factory_id, "name": "Department A"},
        )
        department_b = self.client.post(
            "/api/monitoring/departments",
            headers=admin_headers,
            json={"factory_id": factory_b_id, "name": "Department B"},
        )
        self.assertEqual(department_a.status_code, 201, department_a.text)
        self.assertEqual(department_b.status_code, 201, department_b.text)
        department_a_id = department_a.json()["id"]
        department_b_id = department_b.json()["id"]

        plant_ids = []
        for name, factory_id, department_id in (
            ("Assigned A", self.factory_id, department_a_id),
            ("Assigned B", factory_b_id, department_b_id),
            ("Unassigned B", factory_b_id, department_b_id),
        ):
            plant = self.client.post(
                "/api/monitoring/plants",
                headers=admin_headers,
                json={
                    "name": name,
                    "factory_id": factory_id,
                    "department_id": department_id,
                },
            )
            self.assertEqual(plant.status_code, 201, plant.text)
            plant_ids.append(plant.json()["id"])

        invalid_plant = self.client.post(
            "/api/monitoring/plants",
            headers=admin_headers,
            json={
                "name": "Cross-factory Department",
                "factory_id": self.factory_id,
                "department_id": department_b_id,
            },
        )
        self.assertEqual(invalid_plant.status_code, 422)

        operator = self.client.post(
            "/api/monitoring/users",
            headers=admin_headers,
            json={
                "email": "multi-scope-operator@example.test",
                "password": "secure-test-password",
                "display_name": "Multi-scope Operator",
                "role": "operator",
            },
        )
        self.assertEqual(operator.status_code, 201, operator.text)
        operator_id = operator.json()["id"]
        assigned = self.client.put(
            f"/api/monitoring/users/{operator_id}/organization-scope",
            headers=admin_headers,
            json={
                "factory_ids": [self.factory_id, factory_b_id],
                "department_ids": [department_a_id, department_b_id],
                "plant_ids": plant_ids[:2],
            },
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)
        login = self.client.post(
            "/api/monitoring/auth/login",
            json={
                "email": "multi-scope-operator@example.test",
                "password": "secure-test-password",
            },
        )
        self.assertEqual(login.status_code, 200, login.text)
        operator_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        visible_plants = self.client.get(
            "/api/monitoring/plants",
            headers=operator_headers,
        )
        self.assertEqual(visible_plants.status_code, 200, visible_plants.text)
        self.assertEqual(
            {plant["id"] for plant in visible_plants.json()},
            set(plant_ids[:2]),
        )

    def test_cannot_demote_or_deactivate_last_active_admin(self):
        token = self._create_admin()
        headers = {"Authorization": f"Bearer {token}"}
        with Session(self.engine) as session:
            admin_user = session.scalar(
                select(AlertUser).where(AlertUser.role == "admin")
            )
            self.assertIsNotNone(admin_user)
            admin_id = admin_user.id

        demote_response = self.client.patch(
            f"/api/monitoring/users/{admin_id}",
            headers=headers,
            json={"role": "viewer"},
        )
        self.assertEqual(demote_response.status_code, 409)
        deactivate_response = self.client.patch(
            f"/api/monitoring/users/{admin_id}",
            headers=headers,
            json={"role": "admin", "is_active": False},
        )
        self.assertEqual(deactivate_response.status_code, 409)
        with Session(self.engine) as session:
            admin_user = session.get(AlertUser, admin_id)
            self.assertIsNotNone(admin_user)
            self.assertEqual(admin_user.role, "admin")
            self.assertTrue(admin_user.is_active)

    def test_promoting_user_to_admin_clears_old_plant_assignments(self):
        token = self._create_admin()
        headers = {"Authorization": f"Bearer {token}"}
        plant_response = self.client.post(
            "/api/monitoring/plants",
            headers=headers,
            json={
                "name": "Promotion Plant",
                "timezone": "UTC",
                "factory_id": self.factory_id,
            },
        )
        self.assertEqual(plant_response.status_code, 201, plant_response.text)
        plant_id = plant_response.json()["id"]
        user_response = self.client.post(
            "/api/monitoring/users",
            headers=headers,
            json={
                "email": "promotion-user@example.test",
                "password": "secure-test-password",
                "display_name": "Promotion User",
                "role": "operator",
            },
        )
        self.assertEqual(user_response.status_code, 201, user_response.text)
        user_id = user_response.json()["id"]
        assigned = self.client.put(
            f"/api/monitoring/users/{user_id}/organization-scope",
            headers=headers,
            json={
                "factory_ids": [self.factory_id],
                "department_ids": [],
                "plant_ids": [plant_id],
            },
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)

        promoted = self.client.patch(
            f"/api/monitoring/users/{user_id}",
            headers=headers,
            json={"role": "admin"},
        )
        self.assertEqual(promoted.status_code, 200, promoted.text)
        self.assertEqual(promoted.json()["role"], "admin")
        with Session(self.engine) as session:
            assignments = session.scalars(
                select(AlertUserPlant).where(AlertUserPlant.user_id == user_id)
            ).all()
            self.assertEqual(assignments, [])
            factory_assignments = session.scalars(
                select(AlertUserFactory).where(
                    AlertUserFactory.user_id == user_id
                )
            ).all()
            self.assertEqual(factory_assignments, [])

    def test_alert_lifecycle_history_and_resolution_timestamp(self):
        token = self._create_admin()
        headers = {"Authorization": f"Bearer {token}"}
        plant_response = self.client.post(
            "/api/monitoring/plants",
            headers=headers,
            json={
                "name": "Lifecycle Plant",
                "timezone": "UTC",
                "factory_id": self.factory_id,
            },
        )
        plant_id = plant_response.json()["id"]
        self.client.post(
            "/api/monitoring/rules",
            headers=headers,
            json={
                "name": "Lifecycle threshold",
                "plant_id": plant_id,
                "metric": "defect_rate",
                "operator": "gt",
                "threshold": 5,
            },
        )
        dataset_response = self.client.post(
            "/api/monitoring/datasets",
            headers=headers,
            json={
                "name": "Lifecycle snapshot",
                "plant_id": plant_id,
                "data_rows": [
                    {"Date": "2026-10-01", "Production": 100, "Defects": 10}
                ],
            },
        )
        self.assertEqual(dataset_response.status_code, 201, dataset_response.text)
        alert_id = self.client.get("/api/monitoring/alerts", headers=headers).json()[0]["id"]
        invalid_snooze = self.client.patch(
            f"/api/monitoring/alerts/{alert_id}",
            headers=headers,
            json={"status": "snoozed", "snoozed_until": "2026-10-01T10:00:00"},
        )
        self.assertEqual(invalid_snooze.status_code, 422)

        close_response = self.client.patch(
            f"/api/monitoring/alerts/{alert_id}",
            headers=headers,
            json={"status": "closed", "note": "Đã xử lý."},
        )
        self.assertEqual(close_response.status_code, 200, close_response.text)
        with Session(self.engine) as session:
            resolved_at = session.get(Alert, alert_id).resolved_at
        self.assertIsNotNone(resolved_at)

        invalid_transition = self.client.patch(
            f"/api/monitoring/alerts/{alert_id}",
            headers=headers,
            json={"status": "acknowledged"},
        )
        self.assertEqual(invalid_transition.status_code, 409)
        reopened = self.client.patch(
            f"/api/monitoring/alerts/{alert_id}",
            headers=headers,
            json={"status": "open", "note": "Mở lại."},
        )
        self.assertEqual(reopened.status_code, 200, reopened.text)
        snoozed_until = "2099-10-01T10:00:00+00:00"
        snoozed = self.client.patch(
            f"/api/monitoring/alerts/{alert_id}",
            headers=headers,
            json={"status": "snoozed", "snoozed_until": snoozed_until},
        )
        self.assertEqual(snoozed.status_code, 200, snoozed.text)
        extended_snooze = self.client.patch(
            f"/api/monitoring/alerts/{alert_id}",
            headers=headers,
            json={
                "status": "snoozed",
                "snoozed_until": "2099-10-01T11:00:00+00:00",
            },
        )
        self.assertEqual(extended_snooze.status_code, 200, extended_snooze.text)
        with Session(self.engine) as session:
            self.assertEqual(session.get(Alert, alert_id).resolved_at, resolved_at)
            events = session.scalars(
                select(AlertStatusHistory)
                .where(AlertStatusHistory.alert_id == alert_id)
                .order_by(AlertStatusHistory.id)
            ).all()
            self.assertEqual(
                [event.event_type for event in events],
                [
                    "detected",
                    "status_changed",
                    "reopened",
                    "status_changed",
                    "snooze_updated",
                ],
            )
