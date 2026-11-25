
from urllib.parse import urlsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    APP_ENV: str = "development"
    APP_BASE_URL: str = "http://localhost:3000"
    CORS_ALLOWED_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # Gemini
    GEMINI_API_KEY: str | None = None
    GEMINI_INPUT_COST_PER_MILLION_TOKENS_USD: float | None = Field(
        default=None, ge=0
    )
    GEMINI_OUTPUT_COST_PER_MILLION_TOKENS_USD: float | None = Field(
        default=None, ge=0
    )

    # PostgreSQL
    DATABASE_URL: str | None = None
    DATABASE_HOST_OVERRIDE: str | None = None
    ALERT_AUTH_SECRET: str | None = None
    ALERT_BOOTSTRAP_TOKEN: str | None = None
    ALERT_SCHEDULER_ENABLED: bool = True
    ALERT_SMTP_HOST: str | None = None
    ALERT_SMTP_PORT: int = 587
    ALERT_SMTP_USERNAME: str | None = None
    ALERT_SMTP_PASSWORD: str | None = None
    ALERT_SMTP_FROM: str | None = None
    ALERT_SMTP_USE_TLS: bool = True
    ALERT_WEBHOOK_URL: str | None = None
    SECURITY_RATE_LIMITS_ENABLED: bool = True

    # PostgreSQL-backed hardware analysis queue
    HARDWARE_ANALYSIS_JOBS_ENABLED: bool = True
    HARDWARE_ANALYSIS_JOB_WORKERS: int = Field(default=2, ge=1, le=8)
    HARDWARE_ANALYSIS_JOB_MAX_PENDING: int = Field(default=50, ge=1, le=500)
    HARDWARE_ANALYSIS_JOB_MAX_PENDING_PER_USER: int = Field(
        default=3, ge=1, le=25
    )
    HARDWARE_ANALYSIS_JOB_MAX_ATTEMPTS: int = Field(default=3, ge=1, le=10)
    HARDWARE_ANALYSIS_JOB_POLL_SECONDS: float = Field(
        default=1.0, gt=0, le=30
    )
    HARDWARE_ANALYSIS_JOB_LEASE_SECONDS: int = Field(
        default=3600, ge=60, le=86400
    )

    # Data Quality
    HARDWARE_DATA_QUALITY_MIN_SCORE: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description=(
            "Company data-quality score target (0-100); lower scores "
            "trigger high priority for data-cleanup actions."
        ),
    )

    # Quality target
    HARDWARE_DEFECT_RATE_TARGET: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Company defect-rate target in percent; exceeding it "
            "triggers high priority for relevant quality actions."
        ),
    )

    # Downtime target
    HARDWARE_DOWNTIME_TARGET_MINUTES: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Company downtime target in minutes; exceeding it triggers "
            "high priority for relevant downtime actions."
        ),
    )

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()


def get_database_url() -> URL:
    if not settings.DATABASE_URL:
        raise RuntimeError("DATABASE_URL chưa được cấu hình trong file .env")

    database_url = make_url(settings.DATABASE_URL)
    if settings.DATABASE_HOST_OVERRIDE:
        database_url = database_url.set(host=settings.DATABASE_HOST_OVERRIDE)
    return database_url


def validate_security_settings() -> None:
    environment = settings.APP_ENV.strip().lower()
    if environment not in {"development", "test", "staging", "production"}:
        raise ValueError("APP_ENV must be development, test, staging, or production.")
    if environment != "production":
        return

    secret = settings.ALERT_AUTH_SECRET
    if secret is None or len(secret) < 32:
        raise ValueError(
            "Production requires ALERT_AUTH_SECRET with at least 32 characters."
        )
    if not settings.SECURITY_RATE_LIMITS_ENABLED:
        raise ValueError("Production requires SECURITY_RATE_LIMITS_ENABLED=true.")
    if not settings.DATABASE_URL:
        raise ValueError("Production requires a PostgreSQL DATABASE_URL.")
    try:
        database_backend = make_url(settings.DATABASE_URL).get_backend_name()
    except ArgumentError as error:
        raise ValueError("Production DATABASE_URL is invalid.") from error
    if database_backend != "postgresql":
        raise ValueError("Production requires a PostgreSQL DATABASE_URL.")
    if settings.ALERT_BOOTSTRAP_TOKEN and len(settings.ALERT_BOOTSTRAP_TOKEN) < 32:
        raise ValueError(
            "ALERT_BOOTSTRAP_TOKEN must contain at least 32 characters when set."
        )

    app_url = urlsplit(settings.APP_BASE_URL)
    if (
        app_url.scheme != "https"
        or not app_url.hostname
        or app_url.username
        or app_url.password
        or app_url.query
        or app_url.fragment
    ):
        raise ValueError("Production APP_BASE_URL must be an HTTPS URL.")

    if not settings.CORS_ALLOWED_ORIGINS:
        raise ValueError("Production requires at least one CORS_ALLOWED_ORIGINS entry.")
    for origin in settings.CORS_ALLOWED_ORIGINS:
        parsed_origin = urlsplit(origin)
        if (
            parsed_origin.scheme != "https"
            or not parsed_origin.hostname
            or parsed_origin.username
            or parsed_origin.password
            or parsed_origin.path not in {"", "/"}
            or parsed_origin.query
            or parsed_origin.fragment
        ):
            raise ValueError(
                "Production CORS_ALLOWED_ORIGINS entries must be HTTPS origins."
            )
