import io
import unittest
import zipfile
from unittest.mock import patch

from fastapi import HTTPException, Request
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import get_database_url, settings, validate_security_settings
from app.database.base import Base
from app.models.api_rate_limit import ApiRateLimitBucket
from app.routes.hardware import _parse_hardware_content
from app.services.api_rate_limit import rate_limit_dependency
from app.services.monitoring_auth import ensure_auth_configured


class SecurityHardeningTests(unittest.TestCase):
    def setUp(self):
        self.saved_settings = {
            "APP_ENV": settings.APP_ENV,
            "APP_BASE_URL": settings.APP_BASE_URL,
            "CORS_ALLOWED_ORIGINS": settings.CORS_ALLOWED_ORIGINS,
            "DATABASE_URL": settings.DATABASE_URL,
            "DATABASE_HOST_OVERRIDE": settings.DATABASE_HOST_OVERRIDE,
            "ALERT_AUTH_SECRET": settings.ALERT_AUTH_SECRET,
            "ALERT_BOOTSTRAP_TOKEN": settings.ALERT_BOOTSTRAP_TOKEN,
            "SECURITY_RATE_LIMITS_ENABLED": settings.SECURITY_RATE_LIMITS_ENABLED,
        }

    def tearDown(self):
        for key, value in self.saved_settings.items():
            setattr(settings, key, value)

    def test_database_host_override_preserves_the_rest_of_the_url(self):
        settings.DATABASE_URL = (
            "postgresql+psycopg://user:password@localhost:5432/hardwareai_db"
        )
        settings.DATABASE_HOST_OVERRIDE = "host.docker.internal"

        database_url = get_database_url()

        self.assertEqual(database_url.host, "host.docker.internal")
        self.assertEqual(database_url.port, 5432)
        self.assertEqual(database_url.database, "hardwareai_db")
        self.assertEqual(database_url.username, "user")

    def test_rate_limit_is_atomic_and_does_not_store_client_ip(self):
        settings.ALERT_AUTH_SECRET = "test-rate-limit-secret-with-32-characters"
        settings.SECURITY_RATE_LIMITS_ENABLED = True
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        request = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/test",
                "raw_path": b"/test",
                "query_string": b"",
                "headers": [],
                "client": ("203.0.113.17", 12345),
                "server": ("testserver", 80),
            }
        )
        limiter = rate_limit_dependency(
            "test-route",
            limit=2,
            window_seconds=60,
        )

        try:
            with Session(engine) as db:
                limiter(request, db)
                limiter(request, db)
                with self.assertRaises(HTTPException) as raised:
                    limiter(request, db)

                self.assertEqual(raised.exception.status_code, 429)
                self.assertIn("Retry-After", raised.exception.headers)
                bucket = db.scalar(select(ApiRateLimitBucket))
                self.assertEqual(bucket.request_count, 3)
                self.assertNotIn("203.0.113.17", bucket.bucket_key)
        finally:
            Base.metadata.drop_all(engine)
            engine.dispose()

    def test_production_settings_reject_insecure_origins(self):
        settings.APP_ENV = "production"
        settings.APP_BASE_URL = "https://app.example.test"
        settings.CORS_ALLOWED_ORIGINS = ["http://app.example.test"]
        settings.DATABASE_URL = "postgresql://user:pass@db.example.test/app"
        settings.ALERT_AUTH_SECRET = "test-production-secret-with-at-least-32"
        settings.ALERT_BOOTSTRAP_TOKEN = None
        settings.SECURITY_RATE_LIMITS_ENABLED = True

        with self.assertRaisesRegex(ValueError, "HTTPS origins"):
            validate_security_settings()

    def test_production_settings_require_postgresql(self):
        settings.APP_ENV = "production"
        settings.APP_BASE_URL = "https://app.example.test"
        settings.CORS_ALLOWED_ORIGINS = ["https://app.example.test"]
        settings.DATABASE_URL = "sqlite+pysqlite:///:memory:"
        settings.ALERT_AUTH_SECRET = "test-production-secret-with-at-least-32"
        settings.ALERT_BOOTSTRAP_TOKEN = None
        settings.SECURITY_RATE_LIMITS_ENABLED = True

        with self.assertRaisesRegex(ValueError, "PostgreSQL"):
            validate_security_settings()

    def test_production_settings_accept_secure_configuration(self):
        settings.APP_ENV = "production"
        settings.APP_BASE_URL = "https://app.example.test"
        settings.CORS_ALLOWED_ORIGINS = ["https://app.example.test"]
        settings.DATABASE_URL = "postgresql://user:pass@db.example.test/app"
        settings.ALERT_AUTH_SECRET = "test-production-secret-with-at-least-32"
        settings.ALERT_BOOTSTRAP_TOKEN = None
        settings.SECURITY_RATE_LIMITS_ENABLED = True

        validate_security_settings()

    def test_auth_secret_is_not_generated_automatically_outside_development(self):
        settings.APP_ENV = "test"
        settings.ALERT_AUTH_SECRET = None

        with self.assertRaises(HTTPException) as raised:
            ensure_auth_configured()

        self.assertEqual(raised.exception.status_code, 503)

    def test_parser_rejects_too_many_csv_columns(self):
        with patch("app.routes.hardware.MAX_HARDWARE_COLUMNS", 2):
            with self.assertRaises(HTTPException) as raised:
                _parse_hardware_content(
                    "data.csv",
                    b"Date,Production,Defects\n2026-11-11,10,1\n",
                )

        self.assertEqual(raised.exception.status_code, 413)

    def test_parser_rejects_excessive_office_archive_expansion(self):
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, mode="w") as archive:
            archive.writestr("xl/workbook.xml", "uncompressed content")

        with patch(
            "app.routes.hardware.MAX_ARCHIVE_UNCOMPRESSED_SIZE",
            1,
        ):
            with self.assertRaises(HTTPException) as raised:
                _parse_hardware_content("data.xlsx", archive_buffer.getvalue())

        self.assertEqual(raised.exception.status_code, 413)
