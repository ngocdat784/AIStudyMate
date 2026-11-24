import unittest
from datetime import date, timedelta
from typing import Iterator

import pandas as pd
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models import AnalysisHistory
from app.services.predictive_intelligence_service import (
    analyze_predictive_intelligence,
)


def _predictive_frame(days: int = 40) -> pd.DataFrame:
    start = date(2026, 1, 1)
    return pd.DataFrame(
        [
            {
                "Date": (start + timedelta(days=index)).isoformat(),
                "Machine": "Press 1",
                "Machine Status": "Fault" if index % 4 == 0 else "Running",
                "Downtime Type": "Failure" if index % 4 == 0 else None,
                "Downtime Planned": False,
                "Downtime": 30,
                "Available Time": 480,
                "Operating Time": 450,
                "Production": 100 + 2 * index,
                "Planned Production": 120,
                "Inspected Quantity": 100,
                "Defective Units": 1 + 0.3 * index,
                "Material": "Resin",
                "Material Unit": "kg",
                "Actual Consumption": 1,
                "Material Loss": 0.1,
                "Material Stock": 4,
            }
            for index in range(days)
        ]
    )


def test_predictive_analysis_uses_chronological_statistical_backtests():
    result = analyze_predictive_intelligence(
        _predictive_frame(),
        horizon=7,
        defect_threshold_percent=5,
        capacity_change_percent=10,
        downtime_reduction_percent=50,
        material_shortage_percent=20,
        quality_improvement_percentage_points=2,
    )

    foundation = result["predictive_data_foundation"]
    assert foundation["status"] == "adequate_for_statistical_baseline"
    assert foundation["calendar_coverage_percent"] == 100
    assert foundation["training_dataset"]["leakage_controls"] == {
        "features_use_only_prior_observations": True,
        "splits_are_chronological_and_non_overlapping": True,
        "validation_selects_baseline_method": True,
        "test_set_excluded_from_method_selection": True,
    }

    production = result["forecasts"]["production"]
    assert production["status"] == "forecasted"
    assert production["model_type"] == "statistical_baseline"
    assert production["selected_method"] in {
        "last_observation",
        "rolling_mean_7",
    }
    assert len(production["forecast"]) == 7
    assert production["backtest"]["sample_count"] >= 5
    assert result["production_plan_completion"]["status"] == "forecasted"
    assert len(result["production_plan_completion"]["daily_assessment"]) == 7
    split = production["temporal_split"]
    assert split["train"]["end"] < split["validation"]["start"]
    assert split["validation"]["end"] < split["test"]["start"]
    feature_sample = foundation["training_dataset"]["sample_rows_by_metric"][
        "production"
    ][0]
    assert feature_sample["feature_source_end_period"] < feature_sample["target_period"]
    assert "lag_1_observed_period" in feature_sample["features"]
    assert "material_consumption:Resin|g" in foundation["training_dataset"][
        "temporal_splits_by_metric"
    ]

    assert result["quality_risk"]["threshold_signals"]
    assert result["quality_risk"]["threshold_signals"][-1]["status"] == (
        "threshold_exceedance_signal"
    )
    assert result["machine_failure_assessment"]["status"] == (
        "empirical_frequency_available"
    )
    assert result["machine_failure_assessment"][
        "empirical_failure_probability_percent"
    ] == 25
    assert result["machine_failure_assessment"]["by_machine"][0][
        "empirical_failure_probability_percent"
    ] == 25
    shortage = result["material_shortage_risk"][0]
    assert shortage["status"] == "shortage_signal"
    assert shortage["unit"] == "g"
    assert shortage["available_stock"] == 4000
    assert result["material_quality_risk"]["by_material"][0]["status"] == (
        "forecasted"
    )
    assert result["material_quality_risk"]["by_material"][0][
        "threshold_signals"
    ][-1]["status"] == "threshold_exceedance_signal"

    scenarios = result["scenario_simulation"]["scenarios"]
    baseline_total = sum(
        row["value"] for row in production["forecast"]
    )
    assert scenarios["production_capacity"]["forecast_production"] == round(
        baseline_total * 1.1,
        4,
    )
    assert scenarios["machine_downtime_impact"]["forecast_production"] > baseline_total
    assert scenarios["material_shortage_impact"]["forecast_production"] == round(
        baseline_total * 0.8,
        4,
    )
    assert scenarios["quality_improvement"]["status"] == "simulated"


def test_predictive_analysis_reports_insufficient_history_without_ml():
    result = analyze_predictive_intelligence(_predictive_frame(days=10))

    assert result["predictive_data_foundation"]["status"] == "insufficient_data"
    forecast = result["forecasts"]["production"]
    assert forecast["status"] == "insufficient_data"
    assert forecast["forecast"] == []
    assert forecast["model_type"] == "none"
    assert forecast["prediction_interval"]["status"] == "insufficient_data"
    assert any("Cần ít nhất 15 ngày" in message for message in result["analysis_limitations"])


def test_predictive_analysis_requires_stock_for_material_shortage_risk():
    frame = _predictive_frame()
    frame = frame.drop(columns=["Material Stock"])

    result = analyze_predictive_intelligence(frame)

    assert result["forecasts"]["material_consumption"]["status"] == "forecasted"
    assert result["material_shortage_risk"][0]["status"] == "insufficient_data"
    assert "Material Stock is required" in result["material_shortage_risk"][0]["reason"]


def test_missing_production_values_are_not_forecast_as_zero():
    frame = _predictive_frame()
    frame.loc[:29, "Production"] = None

    result = analyze_predictive_intelligence(frame)

    production = result["forecasts"]["production"]
    assert production["status"] == "insufficient_data"
    assert production["observed_count"] == 10
    assert production["forecast"] == []


class PredictiveIntelligenceAPITests(unittest.TestCase):
    def setUp(self):
        self.saved_settings = {
            "ALERT_AUTH_SECRET": settings.ALERT_AUTH_SECRET,
            "ALERT_BOOTSTRAP_TOKEN": settings.ALERT_BOOTSTRAP_TOKEN,
            "ALERT_SCHEDULER_ENABLED": settings.ALERT_SCHEDULER_ENABLED,
            "HARDWARE_ANALYSIS_JOBS_ENABLED": (
                settings.HARDWARE_ANALYSIS_JOBS_ENABLED
            ),
        }
        settings.ALERT_AUTH_SECRET = "test-predictive-auth-secret-value-32"
        settings.ALERT_BOOTSTRAP_TOKEN = "test-predictive-bootstrap"
        settings.ALERT_SCHEDULER_ENABLED = False
        settings.HARDWARE_ANALYSIS_JOBS_ENABLED = False
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)

        def override_get_db() -> Iterator[Session]:
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        response = self.client.post(
            "/api/monitoring/auth/bootstrap-admin",
            json={
                "email": "predictive-admin@example.test",
                "password": "secure-predictive-password",
                "display_name": "Predictive Admin",
                "bootstrap_token": "test-predictive-bootstrap",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.client.headers.update(
            {"Authorization": f"Bearer {response.json()['access_token']}"}
        )

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        for key, value in self.saved_settings.items():
            setattr(settings, key, value)

    def test_api_persists_insufficient_data_result(self):
        content = _predictive_frame(days=10).to_csv(index=False).encode("utf-8")
        response = self.client.post(
            "/api/predictive-intelligence/analyze",
            data={"horizon": "7"},
            files={"file": ("predictive.csv", content, "text/csv")},
        )

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(
            body["predictive_data_foundation"]["status"],
            "insufficient_data",
        )
        self.assertEqual(body["forecasts"]["production"]["status"], "insufficient_data")
        with Session(self.engine) as session:
            history = session.scalar(
                select(AnalysisHistory).where(
                    AnalysisHistory.id == body["analysis_history_id"]
                )
            )
            self.assertIsNotNone(history)
            assert history is not None
            self.assertEqual(history.analysis_type, "predictive_intelligence")
            self.assertEqual(
                history.result_data["analysis_type"],
                "predictive_intelligence",
            )
