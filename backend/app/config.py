
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Gemini
    GEMINI_API_KEY: str | None = None

    # PostgreSQL
    DATABASE_URL: str | None = None

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
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
